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


def duration_of(node):
    d = child(node, 'duration')
    if d is None: return Fraction(0)
    base = Fraction(d.get('base', '1/4'))
    dots = min(int(d.get('dots', '0')), 4)
    return base * sum((Fraction(1, 2**k) for k in range(dots + 1)), Fraction(0))


def to_div(f):
    value = f * DIVISIONS * 4
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
    for si, sys in enumerate(systems):
        staves = children(child(sys, 'staves'), 'staff')
        if len(staves) != len(parts):
            raise CapellaError(f'System {si+1} has {len(staves)} staves; first system has {len(parts)}. Variable staff count requires a later importer.')
        durations = []
        for pi, staff in enumerate(staves):
            voices = children(child(staff, 'voices'), 'voice')
            if not voices: continue
            voice_lengths = []
            for vi, voice in enumerate(voices):
                t = clock
                for ob in list(child(voice, 'noteObjects')) if child(voice, 'noteObjects') is not None else []:
                    kind = tag(ob)
                    if kind not in ('chord','rest','barline','clefSign','keySign','timeSign'):
                        warnings.append(f'Unrecognized Capella element {kind}; may not be preserved.')
                        continue
                    dur = duration_of(ob) if kind in ('chord','rest') else Fraction(0)
                    parts[pi].events.append(Event(t, kind, ob, si, vi+1, dur))
                    t += dur
                voice_lengths.append(t-clock)
            if len(set(voice_lengths)) > 1:
                warnings.append(f'Uneven voice durations in {parts[pi].name}, system {si+1}; alignment may need review.')
            durations.append(max(voice_lengths))
        if len(set(durations)) > 1:
            warnings.append(f'Uneven staff durations in system {si+1}; original may have independent timing.')
        clock += max(durations) if durations else Fraction(0)
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
            if line == title or (len(line) <= 3 and line.isdigit()):
                continue
            match = re.match(r'^(?:von|by|composed by|composer:)\s+(.+)$', line, re.I)
            if match:
                composer = match.group(1).strip()
            elif height >= 10 and len(line) > 3 and line not in subtitle_lines:
                subtitle_lines.append(line)
    return title, ' — '.join(subtitle_lines), composer

def compute_boundaries(parts, end):
    """Pick explicit barlines and meter-based measures. First system may contain a pickup."""
    first = [e for e in parts[0].events if e.voice == 1]
    first.sort(key=lambda e:(e.time, 0 if e.kind not in ('chord','rest') else 1))
    meter = Fraction(1, 1)
    for e in first:
        if e.kind == 'timeSign':
            try: meter = Fraction(e.node.get('time','4/4'))
            except ValueError: pass
            break
    bars = {Fraction(0), end}
    explicit = {e.time for e in first if e.kind == 'barline'}
    pickup = min((x for x in explicit if 0 < x < meter), default=None)
    offset = pickup or Fraction(0)
    bars.add(offset)
    t = offset
    while t < end:
        t += meter
        if t < end: bars.add(t)
    bars.update(t for t in explicit if 0 <= t <= end)
    return sorted(bars), meter


def add_attributes(measure, key=None, time=None, clef=None, transpose=0, initial=False):
    if not initial and key is None and time is None and clef is None:return
    attrs = sub(measure, 'attributes')
    if initial:sub(attrs, 'divisions', DIVISIONS)
    if key is not None:sub(sub(attrs,'key'), 'fifths',key)
    if time is not None:
        times = sub(attrs,'time')
        numerator,denominator = time.split('/')
        sub(times,'beats',numerator);sub(times,'beat-type',denominator)
    if clef is not None:
        c=sub(attrs,'clef'); mapping = {'treble':('G',2),'bass':('F',4),'alto':('C',3),'tenor':('C',4),'percussion':('percussion',2)}
        sign,line = mapping.get(clef, ('G',2));sub(c,'sign',sign);sub(c,'line',line)
    if initial and transpose:
        tr=sub(attrs,'transpose');sub(tr,'diatonic',0);sub(tr,'chromatic',0);sub(tr,'octave-change',int(transpose/12))


def note_type(dur_node):
    return DURATION_TYPES.get(Fraction(dur_node.get('base','1/4')), 'quarter')


def make_note(meas, event, voice, state, slur_starts, slur_ends):
    """Write MusicXML note elements in MusicXML 4.0 schema order."""
    ob=event.node
    heads = children(child(ob,'heads'),'head') if event.kind=='chord' else [None]
    dnode=child(ob,'duration')
    marks=children(ob,'articulation')
    for hi,head in enumerate(heads):
        note=sub(meas,'note')
        if hi>0:sub(note,'chord')
        accidental=None; shape=None; ties=[]
        if head is None:
            sub(note,'rest')
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
        sub(note,'duration',to_div(event.dur))
        for tp in ties:sub(note,'tie',type=tp)
        sub(note,'voice',voice)
        if dnode is not None:
            sub(note,'type',note_type(dnode))
            for _ in range(int(dnode.get('dots','0'))):sub(note,'dot')
        if accidental:sub(note,'accidental',accidental)
        stem=child(ob,'stem')
        if stem is not None:
            direction=stem.get('dir')
            if direction in ('up','down'):sub(note,'stem',direction)
        if shape in ('diamond','cross','triangle','slash'):
            sub(note,'notehead','x' if shape=='cross' else shape)
        has_notation=bool(marks or ties or (event.time,voice) in slur_starts or (event.time,voice) in slur_ends)
        if has_notation and hi == 0:
            nt=sub(note,'notations')
            for tp in ties:sub(nt,'tied',type=tp)
            for sn in slur_ends.get((event.time,voice),[]):sub(nt,'slur',type='stop',number=sn)
            for sn in slur_starts.get((event.time,voice),[]):sub(nt,'slur',type='start',number=sn)
            if marks:
                articulations=sub(nt,'articulations')
                for a in marks:
                    typ=a.get('type','')
                    tagname={'staccato':'staccato','tenuto':'tenuto','accent':'accent', 'staccatissimo':'staccatissimo','marcato':'strong-accent'}.get(typ)
                    if tagname:sub(articulations,tagname)
    return len(heads)


def make_slur_links(events):
    starts=collections.defaultdict(list);ends=collections.defaultdict(list)
    chords=[e for e in events if e.kind=='chord']
    # A graphical slur with basic noteRange=N begins at the anchoring chord
    # and ends N subsequent chords later (CapXML note ranges are not XML IDs).
    for i,e in enumerate(chords):
        draw=child(e.node,'drawObjects')
        for obj in children(draw,'drawObj'):
            if child(obj,'slur') is None:continue
            basic=child(obj,'basic')
            if basic is None:continue
            span=int(basic.get('noteRange','0'))
            if not (0 < span and i+span < len(chords)):continue
            target=chords[i+span]
            if target.voice!=e.voice:continue
            sn=1 + len(starts[(e.time,e.voice)]) % 6
            starts[(e.time,e.voice)].append(sn);ends[(target.time,e.voice)].append(sn)
    return starts,ends


def serialize_musicxml(raw, layout='balanced'):
    """Returns (MusicXML bytes, report dict)."""
    if layout not in ('balanced','compact','original'):raise CapellaError('Invalid layout mode')
    root=read_capx(raw)
    parts,end,warnings=parse_score(root)
    boundaries,meter=compute_boundaries(parts,end)
    if len(boundaries)<2: raise CapellaError('Could not determine measures')
    title,subtitle,composer=extract_metadata(root)
    score=ET.Element('score-partwise',version='4.0')
    work=sub(score,'work');sub(work,'work-title',title)
    sub(score,'movement-title',title)
    identification=sub(score,'identification')
    if composer:sub(identification,'creator',composer,type='composer')
    defaults=sub(score,'defaults');scaling=sub(defaults,'scaling');sub(scaling,'millimeters','6.2' if layout=='compact' else '7');sub(scaling,'tenths','40')
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
    primary_barlines={e.time:e.node.get('type','regular') for e in parts[0].events if e.kind=='barline' and e.voice==1}
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
            if mi==0:
                add_attributes(measure, key=0, time=str(meter.numerator)+'/'+str(meter.denominator),clef='treble',transpose=p.transpose,initial=True)
            state['accidentals']={}
            vs=sorted({e.voice for e in events if start<=e.time<stop})
            if not vs:vs=[1]
            for vsi,voice in enumerate(vs):
                if vsi:
                    back=sub(measure,'backup');sub(back,'duration',to_div(stop-start))
                ve=[e for e in events if e.voice==voice and start<=e.time<stop]
                for e in ve:
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
                        nnotes+=make_note(measure,e,voice,state,slur_starts,slur_ends)
                        if e.time+e.dur>stop:
                            warnings.append(f'A note crosses a barline in {p.name} at measure {mi+1}.')
            left_repeat=primary_barlines.get(start)=='repBegin'
            if left_repeat or (pi==0 and start in ending_starts):
                bl=sub(measure,'barline',location='left')
                if pi==0 and start in ending_starts:
                    num=ending_starts[start]
                    sub(bl,'ending',f'{num}.',number=num,type='start')
                if left_repeat:sub(bl,'repeat',direction='forward')
            right_type=primary_barlines.get(stop)
            if right_type in ('repEnd','double','end') or (pi==0 and start in ending_starts):
                bl=sub(measure,'barline',location='right')
                if right_type in ('double','end'):
                    sub(bl,'bar-style','light-light' if right_type=='double' else 'light-heavy')
                if pi==0 and start in ending_starts:
                    sub(bl,'ending',number=ending_starts[start],type='stop' if ending_starts[start]=='1' else 'discontinue')
                if right_type=='repEnd':sub(bl,'repeat',direction='backward')
            if mi==0 and start==0 and stop<meter:
                measure.set('implicit','yes')
    # Notation-level warnings: emphasize these instead of silently promising fidelity.
    if root.xpath('//c:volta',namespaces=N):
        warnings.append('Graphical ending brackets have been interpreted as 1st/2nd endings; verify playback order.')
    if root.xpath('//c:trill',namespaces=N):
        warnings.append('Some graphical trill spans are not yet semantically reconstructed.')
    if root.xpath('//c:drawObj/c:text[c:font[@face="capella3"]]',namespaces=N):
        warnings.append('Capella-specific music-font symbols may need manual correction.')
    data=ET.tostring(score,encoding='utf-8',xml_declaration=True,pretty_print=True,doctype='<!DOCTYPE score-partwise  PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">')
    report={'title':title,'subtitle':subtitle,'composer':composer,'parts':[p.name for p in parts],
            'measures':measure_count,'notes':sum(1 for p in parts for e in p.events if e.kind=='chord'),
            'warnings':sorted(set(warnings)),'layout':layout,'source_format':'capx'}
    return data,report
