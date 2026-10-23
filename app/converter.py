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
    if tag(root) != 'score' or ET.QName(root).namespace != CAP_NS:
        raise CapellaError('Only CapXML 2.0 scores are supported by the native converter')
    return root


def parse_score(root):
    warnings = []
    systems = children(child(root, 'systems'), 'system')
    if not systems: raise CapellaError('No systems found in score')
    layouts = children(child(child(root, 'layout'), 'staves'), 'staffLayout')
    layout_map = {x.get('description', f'Part {i+1}'): x for i, x in enumerate(layouts)}
    first_staves = children(child(systems[0], 'staves'), 'staff')
    if not first_staves: raise CapellaError('No staves in score')
    names = [x.get('layout') or f'Part {i+1}' for i, x in enumerate(first_staves)]
    if len(names) != len(set(names)):
        warnings.append('Duplicate staff names found; parts were disambiguated by staff position.')
    parts = []
    for i, name in enumerate(names):
        layout = layout_map.get(name)
        sound = child(layout, 'sound')
        program = (int(sound.get('instr', '0')) + 1) if sound is not None else 1
        trans = int(sound.get('transpose', '0')) if sound is not None else 0
        parts.append(Part(name=name or f'Part {i+1}', layout=name, midi_program=max(1, min(128, program)), transpose=trans))
    clock = Fraction(0)
    current_meter = Fraction(1, 1)
    for si, sys in enumerate(systems):
        staves = children(child(sys, 'staves'), 'staff')
        if len(staves) != len(parts):
            raise CapellaError(f'System {si+1} has {len(staves)} staves; first system has {len(parts)}. Variable staff count requires a later importer.')
        durations = []
        next_meter = current_meter
        for pi, staff in enumerate(staves):
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
    return parts, clock, sorted(set(warnings))


def extract_metadata(root):
    """Extract title, subtitle and author from first-system Capella text.

    Capella often combines subtitle and composer in one multiline text box.
    Preserve those line breaks before selecting the prominent heading.
    """
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
            match = re.match(r'^(?:von|by|composed by|composer:)\s+(.+)$', line, re.I)
            if match:
                composer = match.group(1).strip()
            elif height >= 10 and len(line) > 3 and line not in subtitle_lines:
                subtitle_lines.append(line)
    return title, '\n'.join(subtitle_lines), composer


def extract_variation_numbers(parts):
    """Capella stores section/variation headings as graphical text on chords.

    Keep only prominent, negative-Y numbered annotations in the leading staff,
    not tiny tuplet digits or unrelated score metadata.
    """
    markers = {}
    for event in parts[0].events:
        if event.kind != 'chord' or event.voice != 1:
            continue
        for draw in children(child(event.node, 'drawObjects'), 'drawObj'):
            element = child(draw, 'text')
            font = child(element, 'font')
            content = child(element, 'content')
            if font is None or content is None:
                continue
            label = ' '.join(''.join(content.itertext()).split())
            if not re.fullmatch(
                r'(?i)(?:(?:no|nr|variation|vers)\.?\s*)?[1-9]\d?', label
            ):
                continue
            if font.get('face', '').casefold() in ('capella', 'capella3'):
                continue
            try:
                height = int(font.get('height', 0))
                y = float(element.get('y', '0'))
            except ValueError:
                continue
            if height >= 13 and y < 0:
                markers.setdefault(event.time, label)
    return markers


def add_variation_heading(measure, label):
    """Place a readable, printable Capella variation number on the top staff."""
    direction = sub(measure, 'direction', placement='above')
    direction_type = sub(direction, 'direction-type')
    sub(direction_type, 'words', label, font_size='14',
        font_weight='bold', color='#193B87', default_y='46',
        relative_y='6', justify='left')


def compute_boundaries(parts, end):
    """Use each meter region and explicit source barline as rhythmic anchors.

    Keeping the opening meter for the entire score creates phantom measures
    after a 2/4 -> 6/8 change and places notes across those phantom barlines.
    """
    first = sorted((e for e in parts[0].events if e.voice == 1), key=lambda e:e.time)
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


def make_note(meas, event, voice, state, slur_starts, slur_ends, divisions=DIVISIONS, graphic_links=None, tuplet_links=None, suppress_uniform_strongbeat=False):
    """Write MusicXML note elements in MusicXML 4.0 schema order."""
    ob=event.node
    heads = children(child(ob,'heads'),'head') if event.kind=='chord' else [None]
    dnode=child(ob,'duration')
    marks=children(ob,'articulation')
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
    for hi,head in enumerate(heads):
        note=sub(meas,'note')
        if is_grace:sub(note,'grace',slash='yes')
        if hi>0:sub(note,'chord')
        accidental=None; shape=None; ties=[]
        if head is None:
            sub(note,'rest', **({'measure':'yes'} if is_full_measure_rest else {}))
        else:
            raw=head.get('pitch','C5')
            match=re.fullmatch(r'([A-G])([0-9])',raw)
            if not match:raise CapellaError(f'Unrecognized pitch: {raw}')
            step,cap_oct=match.groups()
            octave=int(cap_oct)-1
            alteration=child(head,'alter')
            if alteration is not None:
                alter=int(alteration.get('step','0'))
                state['accidentals'][(step,octave)]=alter
                accidental={-2:'flat-flat',-1:'flat',0:'natural',1:'sharp',2:'double-sharp'}.get(alter)
            else:
                alter=state['accidentals'].get((step,octave),key_fifths_map(state['key'])[step])
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
        graphical_trill=any(child(d,'trill') is not None for d in
                              children(child(ob,'drawObjects'),'drawObj'))
        has_notation=bool(marks or ties or graphical_trill or id(event) in slur_starts
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
                if graphical_trill:
                    sub(sub(nt,'ornaments'),'trill-mark')
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
            if name not in ('text','slur','volta','trill'):
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
        'tuplets':len(root.xpath('//c:tuplet',namespaces=N)),
        'grace_notes':len(root.xpath('//c:duration[@noDuration="true"]',namespaces=N)),
        'lyrics':len(root.xpath('//c:lyric/c:verse',namespaces=N)),
        'repeat_barlines':len(root.xpath(
            '//c:barline[starts-with(@type,"rep")]',namespaces=N
        )),
        'time_signatures':sorted(set(root.xpath('//c:timeSign/@time',namespaces=N))),
    }
    return feature_counts,dict(sorted(unsupported.items()))


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
                if line is None or 'gliss' not in name or basic is None:
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


def serialize_musicxml(raw, layout='balanced'):
    """Returns (MusicXML bytes, report dict)."""
    if layout not in ('balanced','compact','original'):raise CapellaError('Invalid layout mode')
    root=read_capx(raw)
    parts,end,warnings=parse_score(root)
    uniform_strongbeat=uniformly_marked_voices(parts)
    if uniform_strongbeat:
        names=', '.join(parts[i].name for i in sorted(uniform_strongbeat))
        warnings.append(
            'Uniform Capella strongBeat marks omitted from notation in '
            f'{names}; explicitly selected accents and other articulations remain.'
        )
    boundaries,meter=compute_boundaries(parts,end)
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
    title,subtitle,composer=extract_metadata(root)
    variation_numbers=extract_variation_numbers(parts)
    score=ET.Element('score-partwise',version='4.0')
    work=sub(score,'work');sub(work,'work-title',title)
    sub(score,'movement-title',title)
    identification=sub(score,'identification')
    if composer:sub(identification,'creator',composer,type='composer')
    defaults=sub(score,'defaults');scaling=sub(defaults,'scaling');sub(scaling,'millimeters',{'balanced':'6.4','compact':'5.8','original':'6.0'}[layout]);sub(scaling,'tenths','40')
    page=sub(defaults,'page-layout');sub(page,'page-height','1683.78');sub(page,'page-width','1190.55')
    margins=sub(page,'page-margins',type='both');sub(margins,'left-margin','64');sub(margins,'right-margin','64');sub(margins,'top-margin','64');sub(margins,'bottom-margin','64')
    # Explicit page-one credits render on the score itself. Document metadata
    # alone does not reliably create visible MuseScore title frames on import.
    for kind, value, y, size, weight in (
        ('title', title, 1580, 23, 'bold'),
        ('subtitle', subtitle, 1540, 12, 'normal'),
        ('composer', composer, 1508, 11, 'normal'),
    ):
        if not value or (kind == 'title' and value == 'Converted score'):
            continue
        credit = sub(score, 'credit', page='1')
        sub(credit, 'credit-type', kind)
        sub(credit, 'credit-words', value, default_x='595',
            default_y=y, justify='center', valign='top',
            font_size=size, font_weight=weight)
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
    for event in parts[0].events:
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
    for event in parts[0].events:
        if event.kind != 'chord' or event.voice!=1:continue
        for obj in children(child(event.node,'drawObjects'),'drawObj'):
            volta=child(obj,'volta')
            if volta is not None and volta.get('firstNumber'):
                ending_starts[event.time]=volta.get('firstNumber')

    measure_count=len(boundaries)-1
    # Record each source system's actual *first event*. Looking for any
    # event from a noninitial system marks every later bar as a line break.
    # Capella's graphical systems can occasionally start mid-measure; only
    # request a break when the start aligns with an exported measure.
    source_system_starts = {
        min(e.time for e in parts[0].events if e.system == index)
        for index in {e.system for e in parts[0].events if e.system > 0}
    }
    valid_system_starts = source_system_starts.intersection(boundaries[1:-1])
    if layout == 'original' and len(valid_system_starts) != len(source_system_starts):
        warnings.append('Some Capella system breaks fell inside measures and were omitted to keep notation valid.')
    for pi,p in enumerate(parts):
        pr=sub(score,'part',id=f'P{pi+1}')
        events=sorted(p.events,key=lambda x:(x.time,x.voice))
        slur_starts,slur_ends=make_slur_links(events)
        graphic_links=make_graphic_links(events)
        tuplet_links=make_tuplet_links(events)
        state={'key':0,'time':str(meter.numerator)+'/'+str(meter.denominator),'clef':'treble','accidentals':{}}
        nnotes=0
        for mi in range(measure_count):
            start,stop=boundaries[mi],boundaries[mi+1]
            measure=sub(pr,'measure',number=mi+1)
            # Score-wide system breaks and bar numbers belong only to the
            # leading part. One print instruction per affected measure.
            if pi == 0 and (mi == 0 or (layout == 'original' and start in valid_system_starts)):
                layout_print = sub(measure, 'print', **({'new_system':'yes'} if mi > 0 else {}))
                if mi == 0:
                    sub(layout_print, 'measure-numbering', 'system')
            if pi==0 and start in variation_numbers:
                add_variation_heading(measure,variation_numbers[start])
            if mi==0:
                add_attributes(measure, key=0, time=str(meter.numerator)+'/'+str(meter.denominator),clef='treble',transpose=p.transpose,initial=True,divisions=active_divisions)
            state['accidentals']={}
            vs=sorted({e.voice for e in events if start<=e.time<stop})
            if not vs:vs=[1]
            for vsi,voice in enumerate(vs):
                if vsi:
                    back=sub(measure,'backup');sub(back,'duration',to_div(stop-start,active_divisions))
                ve=[e for e in events if e.voice==voice and start<=e.time<stop]
                cursor=start
                for e in ve:
                    if e.time>cursor:
                        sub(sub(measure,'forward'),'duration',to_div(e.time-cursor,active_divisions))
                        cursor=e.time
                    if e.kind in ('chord','rest') and e.time<cursor:
                        raise CapellaError(
                            f'Overlapping notes in {p.name}, voice {voice}, measure {mi+1}'
                        )
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
                        if e.time+e.dur>stop:
                            raise CapellaError(
                                f'Note crosses a barline in {p.name}, voice {voice}, '
                                f'measure {mi+1}; source measure boundaries need review.'
                            )
                        nnotes+=make_note(measure,e,voice,state,slur_starts,slur_ends,active_divisions,
                                          graphic_links,tuplet_links,
                                          suppress_uniform_strongbeat=pi in uniform_strongbeat)
                        cursor=e.time+e.dur
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
            'variation_numbers':list(variation_numbers.values()),
            'notation_features':features,
            'notation_not_fully_interpreted':unsupported,
            'print_review_recommended':True,
            'normalized_uniform_strongbeat':sum(uniform_strongbeat.values()),
            'repeat_boundaries':{
                'forward':sum(repeat_start(p) for p in boundaries),
                'backward':sum(repeat_end(p) for p in boundaries)
            }}
    return data,report
