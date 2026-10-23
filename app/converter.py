"""Capella CAPX 2.0 -> editable MusicXML 4.0.

Uses score events, not a bitmap/PDF. Capella octave 5 = MusicXML octave 4.
The supported interpretation covers normal notes, rests, barlines, repeats,
partially graphical ornaments, staff and voice identities, slurs and ties.
"""
from __future__ import annotations
import io
import re
import zipfile
import collections
from math import lcm
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from lxml import etree as ET

CAP_NS = 'http://www.capella.de/CapXML/2.0'
N = {'c': CAP_NS}
DIVISIONS = 10080  # supports 1/128 and reasonable tuplets (for whole note use *4)
DURATION_TYPES = {
    Fraction(1, 1): 'whole', Fraction(1, 2): 'half', Fraction(1, 4): 'quarter',
    Fraction(1, 8): 'eighth', Fraction(1, 16): '16th', Fraction(1, 32): '32nd',
    Fraction(1, 64): '64th', Fraction(1, 128): '128th'
}
MAX_XML = 18 * 1024 * 1024


def tag(el):
    return ET.QName(el).localname


def child(el, name):
    return el.find(f'{{{CAP_NS}}}{name}') if el is not None else None


def children(el, name):
    return el.findall(f'{{{CAP_NS}}}{name}') if el is not None else []


def sub(parent, name, text=None, **attrs):
    el = ET.SubElement(parent, name, {k.replace('_', '-'): str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el



def tuplet_normal_notes(tuplet):
    """CapXML tuplet semantics, including tripartite and prolonged groups.

    See CapXML's Tuplet specification: choose powers of 2, or 3*power-of-2
    when tripartite. 'prolong' chooses the smallest *greater* grouping.
    """
    count = int(tuplet.get('count', '3'))
    if count < 2 or count > 32:
        raise CapellaError(f'Unsupported tuplet count: {count}')
    tripartite = tuplet.get('tripartite', 'false').lower() == 'true'
    prolong = tuplet.get('prolong', 'false').lower() == 'true'
    candidates = sorted({(3 if tripartite else 1) * (2 ** exponent)
                         for exponent in range(12)})
    eligible = [n for n in candidates if n > count] if prolong else [
        n for n in candidates if n < count
    ]
    if not eligible:
        raise CapellaError(f'Unrepresentable CapXML tuplet: count={count}')
    return min(eligible) if prolong else max(eligible)


def meter_fraction(raw):
    """Normalize CapXML numeric, common-time and cut-time meters exactly."""
    names={'C':Fraction(4,4),'allaBreve':Fraction(2,2),
           'longAllaBreve':Fraction(4,2)}
    if raw == 'infinite':
        raise CapellaError('Unmetered CapXML requires an explicit-barlines '
                           'importer; cannot infer safe measure durations.')
    try:
        value=names.get(raw) if raw in names else Fraction(raw)
    except (ValueError,ZeroDivisionError) as exc:
        raise CapellaError(f'Unsupported CapXML meter: {raw!r}') from exc
    if value <= 0:
        raise CapellaError(f'Invalid CapXML meter: {raw!r}')
    return value


def duration_of(node, meter=None):
    """Read CapXML time values as sounding fractions of a whole note.

    Integer rest bases count *whole measures*, not whole notes.
    A noDuration grace note occupies no rhythmic space.
    """
    d = child(node, 'duration')
    if d is None or d.get('noDuration') == 'true':
        return Fraction(0)
    base_value = d.get('base', '1/4')
    if tag(node) == 'rest' and meter is not None and re.fullmatch(r'[1-9]\d*', base_value):
        return meter * int(base_value)
    base = Fraction(base_value)
    dots = min(int(d.get('dots', '0')), 4)
    duration = base * sum((Fraction(1, 2**k) for k in range(dots + 1)), Fraction(0))
    tuplet = child(d, 'tuplet')
    if tuplet is not None:
        actual = int(tuplet.get('count', '3'))
        duration *= Fraction(tuplet_normal_notes(tuplet), actual)
    return duration


def to_div(f, divisions=DIVISIONS):
    value = f * divisions * 4
    if value.denominator != 1:
        raise ValueError(f'Unsupported rhythmic subdivision: {f}')
    return value.numerator


def key_fifths_map(fifths):
    out = {step: 0 for step in 'ABCDEFG'}
    order = 'FCGDAEB' if fifths >= 0 else 'BEADGCF'
    for step in order[:min(abs(fifths), 7)]:out[step] = 1 if fifths > 0 else -1
    return out


@dataclass
class Event:
    time: Fraction
    kind: str
    node: object
    system: int
    voice: int = 1
    dur: Fraction = Fraction(0)


@dataclass
class Part:
    name: str
    layout: str
    events: list[Event] = field(default_factory=list)
    midi_program: int = 1
    transpose: int = 0
    present_systems: set[int] = field(default_factory=set)


class CapellaError(ValueError): pass


def read_capx(raw: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            xml_name = next((name for name in names if name == 'score.xml'), None)
            if xml_name is None: raise CapellaError('CAPX archive has no score.xml')
            info = archive.getinfo(xml_name)
            if info.file_size > MAX_XML: raise CapellaError('score.xml is too large')
            xml = archive.read(xml_name)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise CapellaError('Not a valid CAPX ZIP archive') from exc
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
        raise CapellaError('XML DTDs and entities are not permitted')
    try:
        root = ET.fromstring(xml, parser=ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False))
    except ET.XMLSyntaxError as exc:
        raise CapellaError('Invalid score XML') from exc
    source_namespace=ET.QName(root).namespace
    allowed={f'http://www.capella.de/CapXML/{v}.0' for v in (1,2,3,4)}
    if tag(root)!='score' or source_namespace not in allowed:
        raise CapellaError('Unsupported CapXML format: expected CapXML 1.0, 2.0, 3.0 or 4.0')
    if source_namespace != CAP_NS:
        # The notation primitives in these CapXML versions share element
        # names. Normalize element namespaces in-memory, not the raw XML
        # bytes, to avoid accidentally altering text, URLs or attributes.
        # Unknown elements remain subject to the normal notation audit.
        for element in root.iter():
            if isinstance(element.tag,str) and ET.QName(element).namespace==source_namespace:
                element.tag='{'+CAP_NS+'}'+ET.QName(element).localname
        ET.cleanup_namespaces(root)
    root.set('source-capxml-version',source_namespace.rsplit('/',1)[-1])
    return root


def parse_score(root):
    warnings = []
    systems = children(child(root, 'systems'), 'system')
    if not systems: raise CapellaError('No systems found in score')
    layouts = children(child(child(root, 'layout'), 'staves'), 'staffLayout')
    layout_map = {x.get('description', f'Part {i+1}'): x for i, x in enumerate(layouts)}
    first_staves = children(child(systems[0], 'staves'), 'staff')
    if not first_staves: raise CapellaError('No staves in score')
    # Capella may introduce or remove an instrumental staff on a later
    # graphical system (Waggis adds Piccolo 4 only in its final system).
    # Build a stable part list from ALL systems, not only the first.
    names = []
    repeated_names = False
    for si,sys in enumerate(systems):
        system_staves=children(child(sys,'staves'),'staff')
        current=[staff.get('layout') or f'Part {i+1}' for i,staff in enumerate(system_staves)]
        repeated_names |= len(set(current)) != len(current)
        for name in current:
            if name not in names:
                names.append(name)
    if repeated_names:
        # Retain the historical positional fallback for overlapping layout
        # names, rather than collapsing two identically named instruments
        # into one part during the variable-staff discovery pass.
        longest=max((children(child(sys,'staves'),'staff') for sys in systems),
                    key=len)
        names=[staff.get('layout') or f'Part {i+1}'
               for i,staff in enumerate(longest)]
        warnings.append('Duplicate staff layout names found; matching by position may need review.')
    parts = []
    for i,name in enumerate(names):
        layout = layout_map.get(name)
        sound = child(layout, 'sound')
        program = (int(sound.get('instr', '0')) + 1) if sound is not None else 1
        trans = int(sound.get('transpose', '0')) if sound is not None else 0
        parts.append(Part(name=name or f'Part {i+1}', layout=name,
                          midi_program=max(1,min(128,program)), transpose=trans))
    part_positions={p.layout:i for i,p in enumerate(parts)}
    clock = Fraction(0)
    current_meter = Fraction(1, 1)
    for si,sys in enumerate(systems):
        staves = children(child(sys, 'staves'), 'staff')
        if repeated_names:
            # Preserve old position-based behavior if source names are not unique.
            selected=list(enumerate(staves))
            if len(staves)>len(parts):
                raise CapellaError(f'System {si+1} has ambiguous extra staves.')
        else:
            selected=[(part_positions[staff.get('layout') or f'Part {i+1}'],staff)
                      for i,staff in enumerate(staves)]
        if not selected:
            raise CapellaError(f'System {si+1} contains no musical staves.')
        durations = []
        next_meter = current_meter
        for pi, staff in selected:
            parts[pi].present_systems.add(si)
            voices = children(child(staff, 'voices'), 'voice')
            if not voices: continue
            voice_lengths = []
            for vi, voice in enumerate(voices):
                t = clock
                local_meter = current_meter
                for ob in list(child(voice, 'noteObjects')) if child(voice, 'noteObjects') is not None else []:
                    kind = tag(ob)
                    if kind not in ('chord','rest','barline','clefSign','keySign','timeSign'):
                        warnings.append(f'Unrecognized Capella element {kind}; may not be preserved.')
                        continue
                    if kind == 'timeSign':
                        try:
                            local_meter = meter_fraction(ob.get('time', '4/4'))
                        except (ValueError, ZeroDivisionError):
                            raise CapellaError(f'Invalid time signature in system {si+1}')
                        if pi == 0 and vi == 0:
                            next_meter = local_meter
                    dur = duration_of(ob, local_meter) if kind in ('chord','rest') else Fraction(0)
                    parts[pi].events.append(Event(t, kind, ob, si, vi+1, dur))
                    t += dur
                voice_lengths.append(t-clock)
            if len(set(voice_lengths)) > 1:
                warnings.append(f'Uneven voice durations in {parts[pi].name}, system {si+1}; alignment may need review.')
            durations.append(max(voice_lengths))
        if len(set(durations)) > 1:
            warnings.append(f'Uneven staff durations in system {si+1}; original may have independent timing.')
        clock += max(durations) if durations else Fraction(0)
        current_meter = next_meter
    for part in parts:
        if len(part.present_systems)<len(systems):
            warnings.append(
                f'{part.name} appears in {len(part.present_systems)} of '
                f'{len(systems)} Capella systems; the remaining staff measures '
                'will be hidden where the target engraving engine supports it.'
            )
    return parts, clock, sorted(set(warnings))


def first_system_named_credits(root):
    """Recover unambiguous year-tagged authors and additional-voice credits.

    Some scores have a page-level title but keep the author and arranger on
    note-attached text. The narrow author pattern avoids guessing that a
    movement heading or arbitrary first-system text is a composer.
    """
    composer=''
    supplementary=[]
    unambiguous_byline=[]
    for item in root.xpath('//c:systems/c:system[1]//c:drawObj/c:text',namespaces=N):
        font=child(item,'font')
        content=child(item,'content')
        if font is None or content is None:
            continue
        if font.get('face','').casefold() in ('capella','capella3'):
            continue
        label=' '.join(''.join(content.itertext()).split())
        try:
            height=float(font.get('height','0'))
            y=float(item.get('y','0'))
        except ValueError:
            continue
        if not (8<=height<=12 and y<0):
            continue
        if not composer and re.fullmatch(
                r'(?u)(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+\s+){1,3}\d{4}',label):
            composer=label
        elif re.match(r'(?i)^\d+\.\s*(?:schtimm|stimm|stimme)\b',label):
            if label not in supplementary:
                supplementary.append(label)
        elif (y <= -11 and 9 <= height <= 13 and re.fullmatch(
                r'(?u)(?:(?:[A-ZÄÖÜ]\.|[A-ZÄÖÜ][a-zäöüß-]+)\s+){1,3}'
                r'[A-ZÄÖÜ][a-zäöüß-]+',label)):
            # Score authors may be attached to the first staff rather than
            # pageObjects, e.g. "H. Weber" or "Karl Roth". The strict name,
            # size and height rules exclude dates, tune headings and digits.
            unambiguous_byline.append(label)
    return composer or (unambiguous_byline[0] if unambiguous_byline else ''),supplementary


def extract_metadata(root, filename=None):
    """Read Capella page-level credits, falling back to note-attached titles.

    A logo is sometimes embedded as WMF rather than searchable text.
    Avoid mistaking adjacent Intro or copyright strings for a work title.
    """
    page_boxes=[]
    for item in root.xpath('./c:pageObjects/c:drawObj/c:text',namespaces=N):
        font=child(item,'font')
        content=child(item,'content')
        if font is None or content is None: continue
        lines=[v.strip() for v in ''.join(content.itertext()).splitlines() if v.strip()]
        try:
            size=int(font.get('height','0'))
            x=float(item.get('x','0'))
            y=float(item.get('y','0'))
        except ValueError:
            continue
        if (size<8 or y>25 or not lines or
                any('#' in v or re.search(r'\b(?:seite|page)\b',v,re.I)
                    or re.match(r'^\d{1,2}\.\d{1,2}\.\d{4}\b',v)
                    for v in lines)):
            continue
        page_boxes.append((size,x,y,lines))
    # A title in the upper margin is typographically larger than composer
    # credits; don't treat a nearby introductory section label as the title.
    titles=[box for box in page_boxes
            if box[0]>=14 and box[2]<=11 and len(box[3][0])>=4
            and box[3][0].casefold() not in ('intro','trommelsolo')]
    if titles:
        title_box=max(titles,key=lambda box:box[0])
        title=title_box[3][0]
        composer=''
        subtitle_lines=[]
        for size,x,y,lines in page_boxes:
            if (size,x,y,lines)==title_box: continue
            # Dates and rights are page-header metadata, not an invented
            # subtitle or a composer's name. They are printed separately.
            if all(re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}\s+\w{2,8}',v)
                   or v.startswith('©') for v in lines):
                continue
            # Capella may combine a composer and arrangement credit in one
            # multiline page text (e.g. "von W. A. Mozart" followed by
            # "für 4 Piccolo arr. ..."). Preserve both semantic fields.
            if len(lines)>1:
                first=lines[0]
                explicit=re.match(r'(?i)^(?:von|by)\s+(.+)$',first)
                if explicit and not composer:
                    composer=explicit.group(1).strip()
                    subtitle_lines.extend(lines[1:])
                else:
                    subtitle_lines.extend(v for v in lines if v not in subtitle_lines)
                continue
            byline_below=(len(lines)==1 and size>=8 and
                          y>title_box[2] and abs(x-title_box[1])<=15)
            contribution=bool(re.match(
                r'(?i)^(?:arrangiert\b|arr\.|bearb\.|\d+\.\s*(?:schtimm|stimm))',
                lines[0]))
            # A printed byline can name both the original composer and
            # an arranger. Preserve the complete source words *visibly*
            # while writing semantically correct MusicXML creator metadata.
            arr_credit=re.match(r'(?i)^(.+?),\s*arr\.\s+(.+)$',lines[0])
            movement_credit=re.match(
                r'(?i)^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß -]{2,40}\s+v\.\s*'
                r'(.+?),\s*([^,]+),\s*\d{1,2}\.\d{1,2}\.\d{4}$',
                lines[0])
            if (len(lines)==1 and not composer and byline_below
                    and (arr_credit or movement_credit)):
                composer=(arr_credit or movement_credit).group(1).strip()
                subtitle_lines.append(lines[0])
                continue
            if len(lines)==1 and not composer and not contribution and (
                    (x>=title_box[1]+23 and size>=10) or byline_below):
                composer=lines[0]
            elif len(lines)==1 and lines[0].casefold() in ('intro','trommelsolo'):
                continue
            else:
                subtitle_lines.extend(v for v in lines if v not in subtitle_lines)
        extra_composer,extra_lines=first_system_named_credits(root)
        if not composer:
            composer=extra_composer
        for line in extra_lines:
            if line not in subtitle_lines:
                subtitle_lines.append(line)
        return title,'\n'.join(subtitle_lines),composer
    # Capella may store the heading as a graphical metafile. In that case
    # the upload filename is the only reliable machine-readable title.
    has_graphical_title=bool(root.xpath(
        './c:pageObjects/c:drawObj/c:metafile',namespaces=N))
    if has_graphical_title and filename:
        title=re.sub(r'\s*\(\d+\)$','',
                     Path(filename).stem.replace('_',' ').strip()) or 'Converted score'
        composer=''
        subtitles=[]
        for size,x,y,lines in page_boxes:
            if x>=70 and size>=10 and not composer:
                composer=lines[0]
                subtitles.extend(lines[1:])
                break
        if 'Trommeltext:' in composer:
            lead,drums=composer.split('Trommeltext:',1)
            composer=lead.strip()
            subtitles.append('Trommeltext: '+drums.strip())
        return title,'\n'.join(subtitles),composer

    text_boxes = []
    for item in root.xpath('//c:systems/c:system[1]//c:drawObj/c:text', namespaces=N):
        font = child(item, 'font')
        content = child(item, 'content')
        if font is None or content is None:
            continue
        face = font.get('face', '').casefold()
        if face in ('capella', 'capella3'):
            continue  # music glyphs, never score credits
        lines = [' '.join(line.split()) for line in ''.join(content.itertext()).splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        try:
            height = int(font.get('height', '0'))
            y = float(item.get('y', '0'))
        except ValueError:
            continue
        text_boxes.append((height, y, lines))

    if not text_boxes:
        return 'Converted score', '', ''

    # Prefer the largest visible heading, ignoring small bar numbers.
    headings = [box for box in text_boxes if len(box[2][0]) > 3]
    title_box = sorted(headings or text_boxes, key=lambda box: (-box[0], box[1]))[0]
    title = title_box[2][0]
    subtitle_lines = []
    composer = ''
    title_height,title_y,_ = title_box
    for height, y, lines in sorted(text_boxes, key=lambda box: box[1]):
        if (height, y, lines) == title_box:
            # Some Capella documents combine title and byline in one box.
            lines = lines[1:]
        for line in lines:
            # Section labels belong above their measures, not inside the
            # first-page title frame.
            if line == title or re.fullmatch(
                r'(?i)(?:(?:no|nr|variation|vers)\.?\s*)?[1-9]\d?', line
            ):
                continue
            match=re.match(r'^(?:von|by|composed by|composer:)\s+(.+)$',line,re.I)
            credited=re.match(
                r'^((?:[A-ZÄÖÜ][\wäöüß-]+\s+){1,3}[A-ZÄÖÜ][\wäöüß-]+)'
                r'\s+(?=Arr\.?\b|Trommeltext:)',line
            )
            if match:
                composer=match.group(1).strip()
            elif credited and not composer:
                composer=credited.group(1).strip()
                remaining=line[credited.end():].strip()
                if remaining:
                    remaining=re.sub(r'\s+(?=Trommeltext:|Arr\.)','\n',remaining)
                    subtitle_lines.extend(remaining.splitlines())
            elif (not composer and len(lines)==1 and 8 <= height <= 10 and
                  title_y < y <= title_y + 8 and
                  re.fullmatch(r'(?u)[A-ZÄÖÜ][A-Za-zÄÖÜäöüß. ]{3,45}',line)
                  and not re.search(r'(?i)\b(?:trio|intro|solo|tutti)\b',line)):
                # A compact Capella first-system credit (e.g. W. E. Saladin)
                # can use a 9-point font, while the main title uses 16.
                composer=line
            elif (height >= 10 or (height >= 8 and len(lines)>1
                    and title_y <= y <= title_y + 10)) and len(line)>3:
                if line not in subtitle_lines:
                    subtitle_lines.append(line)
    extra_composer,extra_lines=first_system_named_credits(root)
    if not composer:
        composer=extra_composer
    if not composer:
        title_byline=re.search(
            r'(?iu),\s*(?:vom|von)\s+([A-ZÄÖÜ][a-zäöüß-]+\s+'
            r'[A-ZÄÖÜ][a-zäöüß-]+)$',title)
        if title_byline:
            composer=title_byline.group(1)
    for line in extra_lines:
        if line not in subtitle_lines:
            subtitle_lines.append(line)
    return title, '\n'.join(subtitle_lines), composer


def leading_voice_events(parts):
    """Use first AVAILABLE staff of each Capella system as score-wide ruler.

    A source may replace all original staves with a new one in a later system
    (Fyyrvogel ends with Piccolo Zier only). Always using parts[0] would
    silently omit every barline, section label and system break afterwards.
    """
    indices=sorted({e.system for p in parts for e in p.events})
    primary=[]
    for index in indices:
        for p in parts:
            events=[e for e in p.events if e.system==index and e.voice==1]
            if events:
                primary.extend(events)
                break
    return sorted(primary,key=lambda e:(e.time,e.system))


def extract_variation_numbers(parts):
    """Preserve printed section numbers and captions, including grouped graphics.

    Capella stores circled section digits inside a group/ellipse with a
    capella3 digit, whereas some multipart marches place a small movement
    caption on the same chord or on the next short upbeat note.
    Neither should be confused with tuplets, music-font dynamics or credits.
    """
    markers={}
    first=leading_voice_events(parts)
    for index,event in enumerate(first):
        if event.kind not in ('chord','rest') or event.voice!=1:
            continue

        def annotations(source):
            results=[]
            graphics=children(child(source.node,'drawObjects'),'drawObj')
            sibling_ellipse=any(child(obj,'ellipse') is not None for obj in graphics)
            for draw in graphics:
                is_circled=sibling_ellipse or bool(
                    draw.xpath('.//c:group/c:drawObj/c:ellipse',namespaces=N))
                for element in draw.xpath('.//c:text',namespaces=N):
                    font=child(element,'font')
                    content=child(element,'content')
                    if font is None or content is None:
                        continue
                    label=' '.join(''.join(content.itertext()).split())
                    if not label:
                        continue
                    face=font.get('face','').casefold()
                    if face in ('capella','capella3') and not (
                            (is_circled and re.fullmatch(r'[1-9]',label))
                            or (face=='capella3' and re.fullmatch(r'[1-9]\d?',label)
                                and float(font.get('height','0'))>=20
                                and float(element.get('y','0'))<0)):
                        continue
                    try:
                        height=float(font.get('height','0'))
                        y=float(element.get('y','0'))
                    except ValueError:
                        continue
                    results.append((label,height,y,is_circled,
                                    font.get('italic')=='true'))
            return results

        visible=annotations(event)
        for label,height,y,circled,_ in visible:
            numbered=re.fullmatch(
                r'(?i)(?:(?:no|nr|variation|vers)\.?\s*)?[1-9]\d?(?:\s+[^\d].{1,35})?',label
            )
            sectional=re.fullmatch(
                r'(?i)(?:thema|var\.?\s*[1-9]\d?|finale)\b.{0,48}',label
            )
            letter=re.fullmatch(r'[A-Z]',label)
            if not (numbered or sectional or letter):
                continue
            # Circus Märschli uses 12pt letters in real rectangle graphics.
            threshold=(12 if letter and any(
                child(obj,'rectangle') is not None for obj in
                children(child(event.node,'drawObjects'),'drawObj'))
                       else 10 if ' ' in label else 13)
            if height<threshold or y>=0:
                continue
            if circled and re.fullmatch(r'[1-9]',label):
                # Use a printable Unicode circled number rather than silently
                # throwing away the Capella group/ellipse graphic.
                label=chr(0x245f+int(label))
            elif re.fullmatch(r'[1-9]\d?',label):
                nearby=list(visible)
                if index+1<len(first):
                    nxt=first[index+1]
                    if (nxt.kind=='chord' and nxt.system==event.system
                            and Fraction(0)<nxt.time-event.time<=Fraction(1,16)):
                        nearby.extend(annotations(nxt))
                captions=[text for text,size,cy,_,italic in nearby
                          if text!=label and not italic and 8<=size<=12
                          and cy<0 and abs(cy-y)<=5
                          and re.fullmatch(r'[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß .,\'-]{3,55}',text)
                          and not re.search(r'(?i)\b(?:arr\.|von|seite|trommeltext)\b',text)
                          and not re.search(r'\b(?:18|19|20)\d{2}\b',text)
                          and not re.match(r'(?i)^\d+\.\s*(?:schtimm|stimm)',text)]
                if captions:
                    label=f'{label} {captions[0]}'
            markers.setdefault(event.time,label)
    return markers

def extract_score_directions(parts, section_times=None):
    """Keep printed words attached to musical notes or rests.

    Capella stores navigation and performance text such as 'fine' and
    'Trommelsolo' in drawObjects, rather than as dedicated semantic events.
    Avoid music-font glyphs, section numbers and small copyright text.
    """
    out=collections.defaultdict(list)
    section_times=section_times or set()
    for event in leading_voice_events(parts):
        if event.voice!=1 or event.kind not in ('chord','rest'):
            continue
        for draw in children(child(event.node,'drawObjects'),'drawObj'):
            text=child(draw,'text')
            font=child(text,'font')
            content=child(text,'content')
            if font is None or content is None:
                continue
            label=' '.join(''.join(content.itertext()).split())
            if not label or re.fullmatch(r'(?i)(?:(?:no|nr|variation|vers)\.?\s*)?[1-9]\d?',label):
                continue
            if font.get('face','').casefold() in ('capella','capella3'):
                continue
            try:
                height=int(font.get('height','0'))
                y=float(text.get('y','0'))
            except ValueError:
                continue
            # Not every graphical text is a performance instruction.
            # The first-system title/subtitle is often attached to a note;
            # adding it as a direction would print the credits twice.
            performance=re.search(
                r'(?i)(\bfine\b|\bcoda\b|\bsegno\b|'
                r'\bd\.?\s*c\.?\b|\bd\.?\s*s\.?\b|'
                r'\bda\s+capo\b|\bintro\b|\btrommelsolo\b|\btutti\b|\bsolo\b|\btrio\b|'
                r'\britardando\b|\brit\.?\b|\ba tempo\b|'
                r'\baccelerando\b|\baccel\.?\b)',
                label)
            special=re.search(
                r'(?i)^(?:glockeneffekt|gliss(?:ando)?|finale)\b',label
            )
            # Medleys often use a small italic tune caption such as
            # "The chimes of Liberty (Goldman)" on each new theme.
            tune_caption=(y<0 and re.fullmatch(
                r"(?u)[^()\n]{4,65}\s+\([A-ZÄÖÜ][\wÄÖÜäöüß .'-]{2,35}\)",
                label) is not None)
            # A march caption is drawn in italic text beside a numbered
            # section (D'Preusse), not always with an author in parentheses.
            section_caption=(event.time in section_times and 8<=height<=13
                             and y<0 and font.get('italic')=='true'
                             and len(label)>=5 and any(c.isalpha() for c in label))
            if (((performance and (10<=height<=20 or
                                  (label.casefold()=='trio' and 7<=height<=20)))
                 or (special and 8<=height<=20)
                 or (tune_caption and 8<=height<=15)
                 or section_caption)
                    and label not in out[event.time]):
                out[event.time].append(label)
    return dict(out)


def add_score_direction(measure,label):
    direction=sub(measure,'direction',placement='above')
    kind=sub(direction,'direction-type')
    is_tune=bool(re.fullmatch(
        r"(?u)[^()\n]{4,65}\s+\([A-ZÄÖÜ][\wÄÖÜäöüß .'-]{2,35}\)",label))
    if is_tune:
        sub(kind,'words',label,font_size='9',font_style='italic',
            color='#008000',relative_y='16',justify='left')
    else:
        sub(kind,'words',label,font_size='10',font_weight='bold',
            relative_y='16',justify='left')
    # MusicXML playback navigation, in addition to the visible label.
    low=label.casefold()
    sound={}
    if re.search(r'\bd\.?\s*c\.?(?:\s|$)|\bda\s+capo\b',low):
        sound['dacapo']='yes'
    if re.search(r'\bd\.?\s*s\.?(?:\s|$)',low):
        sound['dalsegno']='segno'
    if re.search(r'\b(?:to|al)\s+coda\b',low):
        sound['tocoda']='coda'
    elif re.search(r'\bcoda\b',low):
        sound['coda']='coda'
    if re.search(r'\bsegno\b',low) and 'dalsegno' not in sound:
        sound['segno']='segno'
    if re.fullmatch(r'fine\.?',low.strip()):
        sound['fine']='yes'
    if sound:
        sub(direction,'sound',**sound)


def add_variation_heading(measure, label, boxed=False):
    """Render numbered sections and boxed rehearsal letters."""
    direction = sub(measure, 'direction', placement='above')
    direction_type = sub(direction, 'direction-type')
    props={'font_size':'14','font_weight':'bold','color':'#193B87',
           'default_y':'46','relative_y':'6','justify':'left'}
    if boxed:props['enclosure']='rectangle'
    sub(direction_type,'words',label,**props)


def compute_boundaries(parts, end):
    """Use each meter region and explicit source barline as rhythmic anchors.

    Keeping the opening meter for the entire score creates phantom measures
    after a 2/4 -> 6/8 change and places notes across those phantom barlines.
    """
    first = leading_voice_events(parts)
    signatures = []
    for event in first:
        if event.kind == 'timeSign':
            try:
                meter = meter_fraction(event.node.get('time', '4/4'))
            except (ValueError, ZeroDivisionError):
                raise CapellaError('Invalid time signature in source score')
            signatures.append((event.time,meter))
    if not signatures:
        signatures = [(Fraction(0),Fraction(1, 1))]
    opening = signatures[0][1]
    anchors = {Fraction(0),end}
    anchors.update(e.time for e in first if e.kind=='barline' and 0 <= e.time <= end)
    anchors.update(t for t,_ in signatures if 0 <= t <= end)
    points = sorted(anchors)
    bars = set(points)
    active_meter = opening
    sig_index = 0
    for start,stop in zip(points,points[1:]):
        while sig_index < len(signatures) and signatures[sig_index][0] <= start:
            active_meter = signatures[sig_index][1]
            sig_index += 1
        next_bar = start + active_meter
        while next_bar < stop:
            bars.add(next_bar)
            next_bar += active_meter
    return sorted(bars),opening


def add_attributes(measure, key=None, time=None, clef=None, transpose=0, initial=False, divisions=DIVISIONS):
    if not initial and key is None and time is None and clef is None:return
    attrs = sub(measure, 'attributes')
    if initial:sub(attrs, 'divisions', divisions)
    if key is not None:sub(sub(attrs,'key'), 'fifths',key)
    if time is not None:
        times = sub(attrs,'time')
        if time in ('C','allaBreve','longAllaBreve'):
            times.set('symbol','common' if time=='C' else 'cut')
        if '/' in time:
            numerator, denominator=time.split('/',1)
        else:
            named={'C':('4','4'),'allaBreve':('2','2'),
                   'longAllaBreve':('4','2')}
            numerator, denominator=named[time]
        sub(times,'beats',numerator)
        sub(times,'beat-type',denominator)
    if clef is not None:
        mapping={'treble':('G',2,0),'bass':('F',4,0),
                 'alto':('C',3,0),'tenor':('C',4,0),
                 'percussion':('percussion',2,0)}
        if clef in mapping:
            sign,line,octave_change=mapping[clef]
        else:
            match=re.fullmatch(r'([GFC])([1-5])([+-]?)',clef)
            if not match:
                raise CapellaError(f'Unsupported source clef: {clef!r}')
            sign,line,octave=match.groups()
            line=int(line)
            octave_change={'':0,'+':1,'-':-1}[octave]
        c=sub(attrs,'clef')
        sub(c,'sign',sign);sub(c,'line',line)
        if octave_change:sub(c,'clef-octave-change',octave_change)
    if initial and transpose:
        octaves=int(transpose/12)
        semitones=transpose-(12*octaves)
        scale={0:0,1:0,2:1,3:2,4:2,5:3,6:3,7:4,8:5,9:5,10:6,11:6}
        tr=sub(attrs,'transpose')
        sub(tr,'diatonic',scale[abs(semitones)]*(1 if semitones>=0 else -1))
        sub(tr,'chromatic',semitones)
        if octaves:sub(tr,'octave-change',octaves)


def note_type(dur_node):
    return DURATION_TYPES.get(Fraction(dur_node.get('base','1/4')), 'quarter')


# Single characters from capella3 are music glyph IDs, not literal text.
# Use the same mapping as the MuseScore Capella legacy importer.
CAPELLA_DYNAMICS={
    'p':'p','q':'pp','r':'ppp','f':'f','g':'ff','h':'fff',
    'i':'mp','j':'mf','s':'sf','z':'sfz','{':'fz','|':'fp',
}
CAPELLA_NAVIGATION={
    'd':('D.C.',{'dacapo':'yes'}),
    'e':('D.S.',{'dalsegno':'segno'}),
    'y':('Segno',{'segno':'segno'}),
    '$':('Segno',{'segno':'segno'}),
    'n':('Coda',{'coda':'coda'}),
    'o':('Coda',{'coda':'coda'}),
}


def capella_glyphs(event):
    result=[]
    for graphic in children(child(event.node,'drawObjects'),'drawObj'):
        txt=child(graphic,'text')
        font=child(txt,'font')
        content=child(txt,'content')
        if font is None or content is None:
            continue
        if font.get('face','').casefold() not in ('capella3','capella'):
            continue
        value=''.join(content.itertext()).strip()
        if len(value)==1:
            result.append(value)
    return result


def emit_grouped_metronome_direction(measure,event,voice):
    """Preserve Capella3 quarter tempo from grouped and inline glyphs.

    Fyyrvogel draws the glyph and '=95' in a group; D'Preusse uses
    one inline capella3 graphic containing "¢=100"/"¢=110". In both
    cases this is an explicit *printed* metronome, not an implicit tempo.
    """
    def write_metronome(bpm):
        direction=sub(measure,'direction',placement='above')
        dtype=sub(direction,'direction-type')
        metronome=sub(dtype,'metronome',parentheses='no')
        sub(metronome,'beat-unit','quarter')
        sub(metronome,'per-minute',bpm)
        sub(direction,'voice',voice)
        sub(direction,'sound',tempo=bpm)

    for draw in children(child(event.node,'drawObjects'),'drawObj'):
        words=child(draw,'text')
        content=child(words,'content')
        font=child(words,'font')
        if content is not None and font is not None and (
                font.get('face','').casefold() in ('capella3','capella')):
            label=''.join(content.itertext()).strip()
            matched=re.fullmatch(r'¢\s*=\s*(\d{2,3})',label)
            if matched and 20<=int(matched.group(1))<=360:
                write_metronome(int(matched.group(1)))
        group=child(draw,'group')
        if group is None:
            continue
        glyphs=[]
        labels=[]
        for grouped in children(group,'drawObj'):
            words=child(grouped,'text')
            content=child(words,'content')
            font=child(words,'font')
            if content is None or font is None:
                continue
            value=''.join(content.itertext()).strip()
            if font.get('face','').casefold() in ('capella3','capella'):
                glyphs.append(value)
            else:
                labels.append(value)
        if '¢' not in glyphs:
            continue
        for label in labels:
            matched=re.fullmatch(r'=\s*(\d{2,3})',label)
            if not matched:
                continue
            bpm=int(matched.group(1))
            if not 20<=bpm<=360:
                continue
            write_metronome(bpm)


def emit_glyph_directions(measure,event,voice):
    for glyph in capella_glyphs(event):
        if glyph in CAPELLA_DYNAMICS:
            direction=sub(measure,'direction',placement='below')
            dynamics=sub(sub(direction,'direction-type'),'dynamics')
            sub(dynamics,CAPELLA_DYNAMICS[glyph])
            sub(direction,'voice',voice)
        elif glyph in CAPELLA_NAVIGATION:
            label,sound=CAPELLA_NAVIGATION[glyph]
            direction=sub(measure,'direction',placement='above')
            dtyp=sub(direction,'direction-type')
            if glyph in ('y','$'):sub(dtyp,'segno')
            elif glyph in ('n','o'):sub(dtyp,'coda')
            else:sub(dtyp,'words',label)
            sub(direction,'voice',voice)
            sub(direction,'sound',**sound)


def emit_staff_solo_direction(measure,event,voice):
    for graphic in children(child(event.node,'drawObjects'),'drawObj'):
        txt=child(graphic,'text')
        font=child(txt,'font')
        content=child(txt,'content')
        if font is None or content is None:
            continue
        if font.get('face','').casefold() in ('capella3','capella'):
            continue
        label=' '.join(''.join(content.itertext()).split())
        if not re.match(r'(?i)^solo\b',label):
            continue
        direction=sub(measure,'direction',placement='below')
        sub(sub(direction,'direction-type'),'words',label,font_size='11')
        sub(direction,'voice',voice)


def make_note(meas, event, voice, state, slur_starts, slur_ends, divisions=DIVISIONS, graphic_links=None, tuplet_links=None, suppress_uniform_strongbeat=False):
    """Write MusicXML note elements in MusicXML 4.0 schema order."""
    ob=event.node
    heads = children(child(ob,'heads'),'head') if event.kind=='chord' else [None]
    dnode=child(ob,'duration')
    marks=children(ob,'articulation')
    # Capella's 0xCA music-font symbol (Ê) is a printed accent,
    # distinct from the source's generic strongBeat playback convention.
    glyphs=capella_glyphs(event)
    if event.kind=='chord' and 'Ê' in glyphs and not any(
            'normalAccent' in m.get('type','').split() for m in marks):
        marks=marks+[ET.Element('articulation',type='normalAccent')]
    if suppress_uniform_strongbeat:
        # Widespread strongBeat markers are commonly a source-wide playback/
        # formatting convention, not thousands of individual marcato marks.
        # Retain every other token (e.g. staccato, strongAccent) verbatim.
        marks=[ET.Element('articulation', type=' '.join(
            typ for typ in mark.get('type','').split() if typ != 'strongBeat'))
               for mark in marks]
        marks=[mark for mark in marks if mark.get('type')]
    is_grace = dnode is not None and dnode.get('noDuration') == 'true'
    is_full_measure_rest = (event.kind == 'rest' and dnode is not None
        and re.fullmatch(r'[1-9]\d*',dnode.get('base','')) is not None)
    tuplet = child(dnode, 'tuplet')
    display=child(ob,'display')
    invisible=display is not None and display.get('invisible')=='true'
    for hi,head in enumerate(heads):
        note=sub(meas,'note',**({'print_object':'no'} if invisible else {}))
        if is_grace:sub(note,'grace',slash='yes')
        if hi>0:sub(note,'chord')
        accidental=None; shape=None; ties=[]
        if head is None or (invisible and head.get('silent')=='true'):
            # Silent placeholder heads in older Capella scores are visual
            # spacing/rhythm events, not audible pitched notes. Keep their
            # duration, but don't synthesize a sounding pitch.
            sub(note,'rest', **({'measure':'yes'} if is_full_measure_rest else {}))
        else:
            raw=head.get('pitch','C5')
            match=re.fullmatch(r'([A-G])([0-9])',raw)
            if not match:raise CapellaError(f'Unrecognized pitch: {raw}')
            step,cap_oct=match.groups()
            octave=int(cap_oct)-1
            alteration=child(head,'alter')
            prev=state['accidentals'].get(
                (step,octave),key_fifths_map(state['key'])[step])
            if alteration is not None and alteration.get('step') is not None:
                alter=int(alteration.get('step'))
                state['accidentals'][(step,octave)]=alter
                # Capella frequently writes the key-signature sharp on
                # every F note. A semantic pitch alteration is not always a
                # printed accidental, especially when it is already in force.
                display=alteration.get('display','')
                if display!='suppress' and (
                        alter!=prev or display in ('force','cautionary')):
                    accidental={-2:'flat-flat',-1:'flat',0:'natural',
                                1:'sharp',2:'double-sharp'}.get(alter)
            else:
                # <alter display="suppress"/> explicitly means no new
                # accidental, not a natural sign or a pitch change.
                alter=prev
            pitch=sub(note,'pitch');sub(pitch,'step',step)
            if alter:sub(pitch,'alter',alter)
            sub(pitch,'octave',octave)
            shape=head.get('shape')
            ti=child(head,'tie')
            if ti is not None:
                if ti.get('end')=='true':ties.append('stop')
                if ti.get('begin')=='true':ties.append('start')
        if not is_grace:sub(note,'duration',to_div(event.dur, divisions))
        for tp in ties:sub(note,'tie',type=tp)
        sub(note,'voice',voice)
        if dnode is not None and not is_full_measure_rest:
            sub(note,'type',note_type(dnode))
            for _ in range(int(dnode.get('dots','0'))):sub(note,'dot')
        if accidental:sub(note,'accidental',accidental)
        if tuplet is not None and not is_grace:
            actual=int(tuplet.get('count','3'))
            modified=sub(note,'time-modification')
            sub(modified,'actual-notes',actual)
            sub(modified,'normal-notes',tuplet_normal_notes(tuplet))
        stem=child(ob,'stem')
        if stem is not None:
            direction=stem.get('dir')
            if direction in ('up','down'):sub(note,'stem',direction)
        if shape in ('diamond','cross','triangle','slash'):
            sub(note,'notehead','x' if shape=='cross' else shape)
        gliss_start, gliss_end = graphic_links if graphic_links else ({},{})
        tuplet_start, tuplet_end = tuplet_links if tuplet_links else ({},{})
        graphical_trill=(
            't' in glyphs or '¿' in glyphs or any(child(d,'trill') is not None for d in
                                  children(child(ob,'drawObjects'),'drawObj'))
        )
        fermata_glyph='u' if 'u' in glyphs else 'k' if 'k' in glyphs else None
        upper_mordent='l' in glyphs
        has_notation=bool(marks or ties or graphical_trill or upper_mordent or fermata_glyph or id(event) in slur_starts
                          or id(event) in slur_ends or id(event) in gliss_start
                          or id(event) in gliss_end or id(event) in tuplet_start
                          or id(event) in tuplet_end)
        if ties or (hi == 0 and has_notation):
            nt=sub(note,'notations')
            for tp in ties:sub(nt,'tied',type=tp)
            if hi == 0:
                for sn in slur_ends.get(id(event),[]):sub(nt,'slur',type='stop',number=sn)
                for sn in slur_starts.get(id(event),[]):sub(nt,'slur',type='start',number=sn)
                for tn in tuplet_end.get(id(event),[]):
                    sub(nt,'tuplet',type='stop',number=tn)
                for tn in tuplet_start.get(id(event),[]):
                    sub(nt,'tuplet',type='start',number=tn,bracket='no')
                for sn in gliss_end.get(id(event),[]):
                    sub(nt,'glissando',type='stop',number=sn,line_type='wavy')
                for sn in gliss_start.get(id(event),[]):
                    sub(nt,'glissando',type='start',number=sn,line_type='wavy')
                if graphical_trill or upper_mordent:
                    ornaments=sub(nt,'ornaments')
                    if graphical_trill:sub(ornaments,'trill-mark')
                    if upper_mordent:sub(ornaments,'mordent')
                if fermata_glyph:
                    sub(nt,'fermata',type=('upright' if fermata_glyph=='u' else 'inverted'))
            if marks and hi == 0:
                articulations=sub(nt,'articulations')
                for a in marks:
                    types=a.get('type','').split()
                    mapped={'staccato':'staccato','tenuto':'tenuto',
                        'staccatissimo':'staccatissimo','normalAccent':'accent',
                        'strongAccent':'strong-accent','accent':'accent',
                        'marcato':'strong-accent','weakBeat':'accent',
                        'strongBeat':'strong-accent'}
                    for typ in types:
                        tagname=mapped.get(typ)
                        if tagname:sub(articulations,tagname)
        if hi==0 and not is_grace:
            lyric=child(ob,'lyric')
            for verse in children(lyric,'verse'):
                words=''.join(verse.itertext()).strip()
                if not words:
                    continue
                number=verse.get('i','0')
                lyric_node=sub(note,'lyric',number=str(int(number)+1))
                sub(lyric_node,'syllabic','begin' if verse.get('hyphen')=='true' else 'single')
                sub(lyric_node,'text',words)
    return len(heads)


def make_slur_links(events):
    """Link graphical slurs within their OWN voice, not across other voices.

    CapXML's noteRange indexes subsequent chords, not absolute score note
    indexes. Overlapping slurs need distinct MusicXML numbers.
    """
    starts=collections.defaultdict(list)
    ends=collections.defaultdict(list)
    by_voice=collections.defaultdict(list)
    for event in events:
        if event.kind == 'chord':
            by_voice[event.voice].append(event)
    for voice, chords in by_voice.items():
        candidates=[]
        seen=set()
        for i,event in enumerate(chords):
            for draw in children(child(event.node,'drawObjects'),'drawObj'):
                if child(draw,'slur') is None:
                    continue
                basic=child(draw,'basic')
                if basic is None:
                    continue
                distance=int(basic.get('noteRange','0'))
                end_index=i+distance
                if not 0<distance or end_index>=len(chords):
                    continue
                key=(i,end_index)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(key)
        active={}
        for i,j in sorted(candidates):
            # Reclaim numbers for slurs that have already ended.
            active={number:end for number,end in active.items() if end>=i}
            free=next((n for n in range(1,17) if n not in active),None)
            if free is None:
                raise CapellaError(f'More than 16 overlapping slurs in voice {voice}')
            active[free]=j
            starts[id(chords[i])].append(free)
            ends[id(chords[j])].append(free)
    return starts,ends


def source_notation_audit(root):
    """Inventory notation features, including constructs requiring review."""
    annotations=collections.Counter()
    unsupported=collections.Counter()
    for graphic in root.xpath('//c:systems//c:drawObj | //c:pageObjects//c:drawObj',namespaces=N):
        for element in graphic:
            name=tag(element)
            if name=='basic':
                continue
            annotations[name]+=1
            if name not in ('text','slur','volta','trill','wedge'):
                unsupported[name]+=1
    beams=root.xpath('//c:chord/c:beam',namespaces=N)
    custom_beams=[e for e in beams if e.get('group','auto')!='auto']
    tempos=root.xpath('//c:chord[@tempo]',namespaces=N)
    if custom_beams:
        unsupported['custom_beaming']+=len(custom_beams)
    if tempos:
        unsupported['tempo_changes']+=len(tempos)
    feature_counts={
        'chords':len(root.xpath('//c:chord',namespaces=N)),
        'rests':len(root.xpath('//c:rest',namespaces=N)),
        'ties':len(root.xpath('//c:tie',namespaces=N)),
        'slurs':annotations['slur'],
        'hairpins':annotations['wedge'],
        'tuplets':len(root.xpath('//c:tuplet',namespaces=N)),
        'grace_notes':len(root.xpath('//c:duration[@noDuration="true"]',namespaces=N)),
        'lyrics':len(root.xpath('//c:lyric/c:verse',namespaces=N)),
        'repeat_barlines':len(root.xpath(
            '//c:barline[starts-with(@type,"rep")]',namespaces=N
        )),
        'time_signatures':sorted(set(root.xpath('//c:timeSign/@time',namespaces=N))),
    }
    return feature_counts,dict(sorted(unsupported.items()))


def make_wedge_links(events):
    """Attach Capella graphical crescendo/diminuendo hairpins to note times.

    Wedge noteRange addresses subsequent rhythmic events in the same voice.
    Keep distinct numbers for overlapping wedges; do not infer ornaments
    from unrelated graphic lines.
    """
    starts=collections.defaultdict(list)
    stops=collections.defaultdict(list)
    per_voice=collections.defaultdict(list)
    for event in events:
        if event.kind in ('chord','rest'):
            per_voice[event.voice].append(event)
    serial=0
    for voice,notes in per_voice.items():
        for index,event in enumerate(notes):
            for draw in children(child(event.node,'drawObjects'),'drawObj'):
                wedge=child(draw,'wedge')
                basic=child(draw,'basic')
                if wedge is None or basic is None:continue
                try:
                    span=int(basic.get('noteRange','0'))
                except ValueError:
                    continue
                if span<1 or index+span>=len(notes):
                    continue
                serial+=1
                number=1+((serial-1)%16)
                kind=('diminuendo' if wedge.get('decrescendo')=='true'
                      else 'crescendo')
                raw_color=wedge.get('color','')
                color=('#'+raw_color if re.fullmatch(r'[A-Fa-f0-9]{6}',raw_color)
                       else None)
                starts[id(event)].append((number,kind,color))
                stops[id(notes[index+span])].append(number)
    return starts,stops


def make_graphic_links(events):
    """Semantic glissandi only: explicit named Capella line objects.

    Arbitrary drawn lines and wavy lines are NOT automatically glissandi.
    A named line must also have a source noteRange referencing its target.
    """
    starts=collections.defaultdict(list)
    ends=collections.defaultdict(list)
    by_voice=collections.defaultdict(list)
    for event in events:
        if event.kind=='chord':
            by_voice[event.voice].append(event)
    for voice, chords in by_voice.items():
        for index,event in enumerate(chords):
            for graphic in children(child(event.node,'drawObjects'),'drawObj'):
                name=(graphic.get('name') or '').casefold()
                basic=child(graphic,'basic')
                line=child(graphic,'line')
                wavy=child(graphic,'wavyLine')
                labeled_target=(
                    index+1<len(chords) and any(
                        re.search(r'\bgliss(?:ando)?\b',
                                  ''.join(child(child(g,'text'),'content').itertext()),
                                  re.I)
                        for g in children(child(chords[index+1].node,'drawObjects'),'drawObj')
                        if child(g,'text') is not None
                        and child(child(g,'text'),'content') is not None
                    )
                )
                if basic is None or not (
                        (line is not None and 'gliss' in name)
                        or (wavy is not None and labeled_target)):
                    continue
                span=int(basic.get('noteRange','0'))
                if not 0<span or index+span>=len(chords):
                    continue
                identifier=1+len(starts[id(event)])
                starts[id(event)].append(identifier)
                ends[id(chords[index+span])].append(identifier)
    return starts, ends


def make_tuplet_links(events):
    """MusicXML requires start and stop notations for engraved tuplet numbers."""
    starts=collections.defaultdict(list)
    stops=collections.defaultdict(list)
    voices=collections.defaultdict(list)
    for event in events:
        if event.kind in ('chord','rest'):
            voices[event.voice].append(event)
    for voice,notes in voices.items():
        i=0
        while i<len(notes):
            d=child(notes[i].node,'duration')
            tuplet=child(d,'tuplet')
            if tuplet is None:
                i+=1
                continue
            count=int(tuplet.get('count','3'))
            run=notes[i:i+count]
            if (len(run)==count and all(
                (child(child(x.node,'duration'),'tuplet') is not None
                 and child(child(x.node,'duration'),'tuplet').get('count')==str(count))
                for x in run
            )):
                starts[id(run[0])].append(1)
                stops[id(run[-1])].append(1)
                i+=count
            else:
                i+=1
    return starts,stops


def uniformly_marked_voices(parts, articulation='strongBeat', minimum_chords=32):
    """Find parts with a near-universal source articulation on their chords.

    Distinguish implicit score-wide defaults from deliberate, isolated accents.
    Only the ubiquitous token is omitted; other articulations remain editable.
    """
    affected = {}
    for index, part in enumerate(parts):
        chords = [event for event in part.events if event.kind == 'chord']
        if len(chords) < minimum_chords:
            continue
        applied = sum(
            articulation in mark.get('type','').split()
            for event in chords
            for mark in children(event.node, 'articulation')
        )
        if applied >= len(chords) * 0.98:
            affected[index] = applied
    return affected


def serialize_musicxml(raw, layout='balanced', filename=None):
    """Returns (MusicXML bytes, report dict)."""
    if layout not in ('balanced','compact','original'):raise CapellaError('Invalid layout mode')
    root=read_capx(raw)
    parts,end,warnings=parse_score(root)
    original_capxml=root.get('source-capxml-version','2.0')
    if original_capxml!='2.0':
        warnings.append(
            f'CapXML {original_capxml} source normalized to the common '
            'notation model; review proprietary graphics and advanced symbols.'
        )
    uniform_strongbeat=uniformly_marked_voices(parts)
    if uniform_strongbeat:
        names=', '.join(parts[i].name for i in sorted(uniform_strongbeat))
        warnings.append(
            'Uniform Capella strongBeat marks omitted from notation in '
            f'{names}; explicitly selected accents and other articulations remain.'
        )
    boundaries,meter=compute_boundaries(parts,end)
    # A time signature's written form is semantically significant: 2/4 is
    # NOT displayed as 1/2, and 6/8 is NOT displayed as 3/4, even though
    # both pairs have equal Fraction values.
    opening_time=next((
        event.node.get('time','4/4')
        for event in leading_voice_events(parts)
        if event.kind=='timeSign' and event.time==0
    ),str(meter.numerator)+'/'+str(meter.denominator))
    if len(boundaries)<2: raise CapellaError('Could not determine measures')
    # CapXML permits tuplets whose prime denominators (e.g. 11, 13)
    # cannot be represented by the old fixed 10080 divisions/quarter.
    # Select exact divisions for this score; never round note timings.
    active_divisions = DIVISIONS
    for part in parts:
        for event in part.events:
            if event.dur:
                active_divisions = lcm(active_divisions,(event.dur*4).denominator)
    for begin, finish in zip(boundaries, boundaries[1:]):
        active_divisions = lcm(active_divisions,((finish-begin)*4).denominator)
    if active_divisions > 5_000_000:
        raise CapellaError('Score requires an impractically fine rhythmic grid; '
                           'cannot safely encode it in MusicXML.')
    features,unsupported=source_notation_audit(root)
    title,subtitle,composer=extract_metadata(root, filename)
    variation_numbers=extract_variation_numbers(parts)
    boxed_sections=set()
    for event in leading_voice_events(parts):
        label=variation_numbers.get(event.time)
        if (label and re.fullmatch(r'[A-Z]',label) and any(
                child(obj,'rectangle') is not None for obj in
                children(child(event.node,'drawObjects'),'drawObj'))):
            boxed_sections.add(event.time)
    score_directions=extract_score_directions(parts,set(variation_numbers))
    # Short standalone page-level introduction labels are not score credits.
    # Capella 3.0 scores such as Fyyrvogel place 'Intro' just above the
    # first system, rather than attaching it to a note.
    for item in root.xpath('./c:pageObjects/c:drawObj/c:text',namespaces=N):
        content=child(item,'content')
        if content is None:continue
        label=' '.join(''.join(content.itertext()).split())
        try:
            y=float(item.get('y','0'))
        except ValueError:
            continue
        if (8 <= y <= 18 and label.casefold() in ('intro','trommelsolo')
                and label not in score_directions.get(Fraction(0),[])):
            score_directions.setdefault(Fraction(0),[]).append(label)
    score=ET.Element('score-partwise',version='4.0')
    work=sub(score,'work');sub(work,'work-title',title)
    sub(score,'movement-title',title)
    identification=sub(score,'identification')
    if composer:sub(identification,'creator',composer,type='composer')
    for item in root.xpath('./c:pageObjects/c:drawObj/c:text',namespaces=N):
        content=child(item,'content')
        if content is None:continue
        raw=' '.join(''.join(content.itertext()).split())
        if raw.startswith('©'):
            sub(identification,'rights',raw)
    for credit_line in subtitle.splitlines():
        arranged=re.search(
            r'(?i)(?:^|[\s,])(?:arrangiert\s+(?:vom|von)|arr\.\s*(?:von)?|bearb\.\s*(?:von)?)\s+(.+)$',
            credit_line.strip())
        if arranged:
            name=re.sub(r'(?:,\s*|\s+)(?:18|19|20)\d{2}\s*$','',arranged.group(1).strip())
            sub(identification,'creator',name,type='arranger')
    defaults=sub(score,'defaults');scaling=sub(defaults,'scaling');sub(scaling,'millimeters',{'balanced':'6.4','compact':'5.8','original':'6.0'}[layout]);sub(scaling,'tenths','40')
    page=sub(defaults,'page-layout');sub(page,'page-height','1683.78');sub(page,'page-width','1190.55')
    margins=sub(page,'page-margins',type='both');sub(margins,'left-margin','64');sub(margins,'right-margin','64');sub(margins,'top-margin','64');sub(margins,'bottom-margin','64')
    # Explicit page-one credits render on the score itself. Document metadata
    # alone does not reliably create visible MuseScore title frames on import.
    # Don't print the composer twice when the original byline already
    # contains the composer and arranger ("Mozart, arr. ...").
    compact=lambda x: re.sub(r'[\W_]+','',x,flags=re.UNICODE).casefold()
    display_composer=('' if composer and
                      (compact(composer) in compact(subtitle)
                       or compact(composer) in compact(title))
                      else composer)
    for kind, value, y, size, weight in (
        ('title', title, 1580, 23, 'bold'),
        ('subtitle', subtitle, 1540, 12, 'normal'),
        ('composer', display_composer, 1508, 11, 'normal'),
    ):
        if not value or (kind == 'title' and value == 'Converted score'):
            continue
        credit = sub(score, 'credit', page='1')
        sub(credit, 'credit-type', kind)
        sub(credit, 'credit-words', value, default_x='595',
            default_y=y, justify='center', valign='top',
            font_size=size, font_weight=weight)
    # Preserve source header timestamps and copyright in their own
    # positions rather than as additional centered subtitle lines.
    for item in root.xpath('./c:pageObjects/c:drawObj/c:text',namespaces=N):
        content=child(item,'content')
        if content is None:continue
        raw=' '.join(''.join(content.itertext()).split())
        if raw.startswith('©'):
            x,y,align='64','1615','left'
        elif re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}\s+\w{2,8}',raw):
            x,y,align='1126','1615','right'
        else:
            continue
        credit=sub(score,'credit',page='1')
        sub(credit,'credit-type','rights' if raw.startswith('©') else 'other')
        sub(credit,'credit-words',raw,default_x=x,default_y=y,
            justify=align,valign='top',font_size='9')
    # Preserve closing attribution/dedication text that Capella anchors
    # to the last system, rather than to the first-page pageObjects.
    # Olymper and several Basel marches otherwise lose their final credits.
    source_page_count=None
    for box in root.xpath('./c:pageObjects/c:drawObj/c:text',namespaces=N):
        content=child(box,'content')
        if content is None:continue
        pagination=' '.join(''.join(content.itertext()).split())
        count_match=re.search(r'#\s*/\s*(\d{1,3})\b',pagination)
        if count_match:
            source_page_count=int(count_match.group(1))
            break
    if source_page_count and 1<=source_page_count<=200:
        last_systems=root.xpath('./c:systems/c:system[last()]',namespaces=N)
        if last_systems:
            closing_credits=[]
            for words in last_systems[0].xpath('.//c:drawObj/c:text',
                                                 namespaces=N):
                font=child(words,'font')
                content=child(words,'content')
                if font is None or content is None:continue
                if font.get('face','').casefold() in ('capella','capella3'):
                    continue
                label=' '.join(''.join(content.itertext()).split())
                try:
                    size=float(font.get('height','0'))
                    y=float(words.get('y','0'))
                except ValueError:
                    continue
                if (7<=size<=12 and y>=4.5 and len(label)>=10 and
                        any(ch.isalpha() for ch in label) and
                        label not in closing_credits):
                    closing_credits.append(label)
            for i,label in enumerate(closing_credits):
                credit=sub(score,'credit',page=source_page_count)
                sub(credit,'credit-type','other')
                sub(credit,'credit-words',label,
                    default_x=('64' if i%2==0 else '1126'),
                    default_y=str(64+(i//2)*20),
                    justify=('left' if i%2==0 else 'right'),
                    valign='bottom',font_size='8')
    plist=sub(score,'part-list')
    # Respect Capella's multi-staff brace/bracket grouping. This is structural,
    # and avoids MuseScore displaying four unrelated solo parts.
    bracket_elements = children(child(child(root,'layout'),'brackets'),'bracket')
    grouping = any(int(b.get('from','-1')) == 0 and int(b.get('to','-1')) == len(parts)-1 for b in bracket_elements)
    if grouping:
        group=sub(plist,'part-group',type='start',number='1')
        sub(group,'group-symbol','bracket');sub(group,'group-barline','yes')
    for pi,p in enumerate(parts):
        sp=sub(plist,'score-part',id=f'P{pi+1}')
        sub(sp,'part-name',p.name);sub(sp,'part-abbreviation',f'P{pi+1}')
        inst=sub(sp,'score-instrument',id=f'I{pi+1}');sub(inst,'instrument-name',p.name)
        midi=sub(sp,'midi-instrument',id=f'I{pi+1}');sub(midi,'midi-channel',(pi%15)+1);sub(midi,'midi-program',p.midi_program)
    if grouping:sub(plist,'part-group',type='stop',number='1')
    # Boundary markers can be supplied twice at the same timestamp:
    # a repeat end at the end of a system and a repeat begin at the next.
    # Do NOT store them in a time->single-type dictionary: last-write-wins
    # drops the backward repeat (seven times in Alte Schwytzer).
    boundary_marks=collections.defaultdict(set)
    for event in leading_voice_events(parts):
        if event.kind=='barline' and event.voice==1:
            boundary_marks[event.time].add(event.node.get('type','single'))
    def repeat_start(position):
        return bool(boundary_marks[position] &
                    {'repBegin','repEndBegin'})
    def repeat_end(position):
        return bool(boundary_marks[position] &
                    {'repEnd','repEndBegin'})
    def other_barline(position):
        types=boundary_marks[position]
        return 'end' if 'end' in types else 'double' if 'double' in types else (
            'dashed' if 'dashed' in types else None
        )
    # Most Capella ending brackets are attached graphically to the first chord
    # of the ending measure. Their firstNumber is a semantic indicator.
    ending_starts={}
    for event in leading_voice_events(parts):
        if event.kind != 'chord' or event.voice!=1:continue
        for obj in children(child(event.node,'drawObjects'),'drawObj'):
            volta=child(obj,'volta')
            if volta is not None and volta.get('firstNumber'):
                ending_starts[event.time]=volta.get('firstNumber')

    # Staves introduced or removed later need a system boundary even when
    # the user selected Balanced/Compact; otherwise a fourth part can appear
    # halfway through an existing printed system.
    # This must cover EVERY system, including systems lacking the first part.
    indexed_system_starts = {}
    seen_systems=set()
    for event in leading_voice_events(parts):
        if event.system not in seen_systems:
            seen_systems.add(event.system)
            indexed_system_starts[event.time]=event.system
    staff_transition_starts = {
        t for t,si in indexed_system_starts.items()
        if si > 0 and any(
            (si in part.present_systems) != (si-1 in part.present_systems)
            for part in parts
        )
    }
    measure_count=len(boundaries)-1
    # Record each source system's actual *first event*. Looking for any
    # event from a noninitial system marks every later bar as a line break.
    # Capella's graphical systems can occasionally start mid-measure; only
    # request a break when the start aligns with an exported measure.
    source_system_starts = {
        time for time,si in indexed_system_starts.items() if si>0
    }
    valid_system_starts = source_system_starts.intersection(boundaries[1:-1])
    if layout == 'original' and len(valid_system_starts) != len(source_system_starts):
        warnings.append('Some Capella system breaks fell inside measures and were omitted to keep notation valid.')
    for pi,p in enumerate(parts):
        pr=sub(score,'part',id=f'P{pi+1}')
        events=sorted(p.events,key=lambda x:(x.time,x.voice))
        slur_starts,slur_ends=make_slur_links(events)
        graphic_links=make_graphic_links(events)
        wedge_starts,wedge_stops=make_wedge_links(events)
        tuplet_links=make_tuplet_links(events)
        state={'key':0,'time':opening_time,'clef':'treble','accidentals':{}}
        nnotes=0
        for mi in range(measure_count):
            start,stop=boundaries[mi],boundaries[mi+1]
            measure=sub(pr,'measure',number=mi+1)
            # Score-wide system breaks and bar numbers belong only to the
            # leading part. One print instruction per affected measure.
            new_system = mi>0 and (
                (layout == 'original' and start in valid_system_starts)
                or start in staff_transition_starts
            )
            if pi == 0 and (mi == 0 or new_system):
                layout_print = sub(measure, 'print',
                                   **({'new_system':'yes'} if new_system else {}))
                if mi == 0:
                    sub(layout_print, 'measure-numbering', 'system')
            if pi==0 and start in variation_numbers:
                add_variation_heading(measure,variation_numbers[start],
                                      boxed=start in boxed_sections)
            if mi==0:
                add_attributes(measure, key=0, time=opening_time,clef='treble',transpose=p.transpose,initial=True,divisions=active_divisions)
            # A staff which exists only in later Capella systems must not
            # appear throughout the entire score. MusicXML staff-details
            # controls its visibility independently of musical durations.
            visibility = None
            if mi==0 and 0 not in p.present_systems:
                visibility='no'
            elif start in indexed_system_starts:
                si=indexed_system_starts[start]
                if si>0 and (si in p.present_systems)!=(si-1 in p.present_systems):
                    visibility='yes' if si in p.present_systems else 'no'
            if visibility is not None:
                sub(sub(measure,'attributes'),'staff-details',print_object=visibility)
            state['accidentals']={}
            vs=sorted({e.voice for e in events if start<=e.time<stop})
            if not vs:vs=[1]
            for vsi,voice in enumerate(vs):
                if vsi:
                    back=sub(measure,'backup');sub(back,'duration',to_div(stop-start,active_divisions))
                ve=[
                    e for e in events if e.voice==voice and (
                        start<=e.time<stop or (
                            e.kind=='rest' and e.time<start and
                            e.time+e.dur>start and
                            re.fullmatch(r'[1-9]\d*',
                                (child(e.node,'duration').get('base','')
                                 if child(e.node,'duration') is not None else ''))
                        )
                    )
                ]
                cursor=start
                placed_directions=set()
                for e in ve:
                    if e.time>cursor:
                        sub(sub(measure,'forward'),'duration',to_div(e.time-cursor,active_divisions))
                        cursor=e.time
                    if (e.kind in ('chord','rest') and e.time<cursor
                            and not (e.kind=='rest' and e.time<start)):
                        raise CapellaError(
                            f'Overlapping notes in {p.name}, voice {voice}, measure {mi+1}'
                        )
                    if e.kind in ('chord','rest') and e.time>=start:
                        emit_glyph_directions(measure,e,voice)
                        emit_grouped_metronome_direction(measure,e,voice)
                        if pi>0:
                            emit_staff_solo_direction(measure,e,voice)
                        for no in wedge_stops.get(id(e),()):
                            direction=sub(measure,'direction',placement='below')
                            dtype=sub(direction,'direction-type')
                            sub(dtype,'wedge',number=no,type='stop')
                            sub(direction,'voice',voice)
                        for no,kind,color in wedge_starts.get(id(e),()):
                            direction=sub(measure,'direction',placement='below')
                            dtype=sub(direction,'direction-type')
                            props={'number':no,'type':kind}
                            if color:props['color']=color
                            sub(dtype,'wedge',**props)
                            sub(direction,'voice',voice)
                    if (pi==0 and voice==1 and e.kind in ('chord','rest')
                            and e.time not in placed_directions):
                        for words in score_directions.get(e.time,()):
                            add_score_direction(measure,words)
                        placed_directions.add(e.time)
                    # Capella's @tempo is a playback setting, not necessarily
                    # a printed mark; keep it as an independent MusicXML
                    # <sound> instruction without adding visible text.
                    if e.kind=='chord' and e.node.get('tempo') is not None:
                        tempo=e.node.get('tempo','')
                        if re.fullmatch(r'\d{2,3}(?:\.\d+)?',tempo):
                            if 20<=float(tempo)<=360:
                                sub(measure,'sound',tempo=tempo)

                    if e.kind=='keySign':
                        fifths=int(e.node.get('fifths',0));prev_fifths=state['key'];state['key']=fifths
                        if mi==0 or fifths!=prev_fifths: add_attributes(measure,key=fifths)
                    elif e.kind=='timeSign':
                        current=e.node.get('time','4/4')
                        if state['time']!=current:add_attributes(measure,time=current)
                        state['time']=current
                    elif e.kind=='clefSign':
                        clef=e.node.get('clef','treble')
                        if clef!=state['clef']:add_attributes(measure,clef=clef)
                        state['clef']=clef
                    elif e.kind in ('chord','rest'):
                        rest_duration=child(e.node,'duration')
                        rest_base=(rest_duration.get('base','')
                                   if rest_duration is not None else '')
                        is_multi_rest=(e.kind=='rest'
                                       and re.fullmatch(r'[2-9]\d*',rest_base) is not None)
                        event_for_note=e
                        if is_multi_rest and e.time<=start and e.time+e.dur>=stop:
                            # A source seven-/eight-measure rest must be
                            # represented in each destination measure, not
                            # treated as a giant single note crossing bars.
                            event_for_note=Event(start,e.kind,e.node,e.system,
                                                 e.voice,stop-start)
                            if start==e.time:
                                style=sub(sub(measure,'attributes'),'measure-style')
                                sub(style,'multiple-rest',rest_base)
                        elif e.time+e.dur>stop:
                            raise CapellaError(
                                f'Note crosses a barline in {p.name}, voice {voice}, '
                                f'measure {mi+1}; source measure boundaries need review.'
                            )
                        nnotes+=make_note(measure,event_for_note,voice,state,
                                          slur_starts,slur_ends,active_divisions,
                                          graphic_links,tuplet_links,
                                          suppress_uniform_strongbeat=pi in uniform_strongbeat)
                        cursor=event_for_note.time+event_for_note.dur
                if cursor<stop:
                    sub(sub(measure,'forward'),'duration',to_div(stop-cursor,active_divisions))
            left_repeat=repeat_start(start)
            if left_repeat or (pi==0 and start in ending_starts):
                bl=sub(measure,'barline',location='left')
                if pi==0 and start in ending_starts:
                    num=ending_starts[start]
                    sub(bl,'ending',f'{num}.',number=num,type='start')
                if left_repeat:sub(bl,'repeat',direction='forward')
            right_type=other_barline(stop)
            right_repeat=repeat_end(stop)
            if right_repeat or right_type or (pi==0 and start in ending_starts):
                bl=sub(measure,'barline',location='right')
                if right_type:
                    sub(bl,'bar-style',{'double':'light-light','end':'light-heavy',
                                        'dashed':'dashed'}[right_type])
                if pi==0 and start in ending_starts:
                    sub(bl,'ending',number=ending_starts[start],type='stop' if ending_starts[start]=='1' else 'discontinue')
                if right_repeat:sub(bl,'repeat',direction='backward')
            if mi==0 and start==0 and stop<meter:
                measure.set('implicit','yes')
    # Notation-level warnings: emphasize these instead of silently promising fidelity.
    if root.xpath('//c:systems//c:volta | //c:pageObjects//c:volta',namespaces=N):
        warnings.append('Some graphical ending brackets may need layout review.')
    if root.xpath('//c:systems//c:drawObj[c:trill]/c:basic[number(@noteRange)>1]',namespaces=N):
        warnings.append('Extended graphical trill lines were reduced to trill markings; review their endpoints.')
    if root.xpath('//c:systems//c:drawObj/c:text[c:font[@face="capella3"]]',namespaces=N):
        warnings.append('Capella-specific music-font symbols may need manual correction.')
    data=ET.tostring(score,encoding='utf-8',xml_declaration=True,pretty_print=True,doctype='<!DOCTYPE score-partwise  PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">')
    for name,count in unsupported.items():
        warnings.append(f'{count} source {name} elements require manual notation review.')
    report={'title':title,'subtitle':subtitle,'composer':composer,'parts':[p.name for p in parts],
            'measures':measure_count,'notes':sum(1 for p in parts for e in p.events if e.kind=='chord'),
            'warnings':sorted(set(warnings)),'layout':layout,'source_format':'capx',
            'source_systems':len(children(child(root, 'systems'), 'system')),
            'capxml_version':original_capxml,
            'variation_numbers':list(variation_numbers.values()),
            'printed_directions':[word for words in score_directions.values() for word in words],
            'notation_features':features,
            'notation_not_fully_interpreted':unsupported,
            'print_review_recommended':True,
            'normalized_uniform_strongbeat':sum(uniform_strongbeat.values()),
            'variable_staff_systems':any(
                len(p.present_systems)!=len(children(child(root,'systems'),'system'))
                for p in parts),
            'repeat_boundaries':{
                'forward':sum(repeat_start(p) for p in boundaries),
                'backward':sum(repeat_end(p) for p in boundaries)
            }}
    return data,report
