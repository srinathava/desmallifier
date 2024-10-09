import dxfgrabber
from fpdf import FPDF
import math
from dataclasses import dataclass, replace
import argparse
import copy

@dataclass
class Params:
    orientation: str = "landscape"
    scale: float = 2.5
    overlap: float = 0.5
    margin: float = 0.25

    def __post_init__(self):
        if self.orientation == "landscape":
            self.page_h = 8.5
            self.page_w = 11
        else:
            self.page_h = 11
            self.page_w = 8.5

        self.cutx = (self.page_w - self.overlap - 2*self.margin)/self.scale
        self.cuty = (self.page_h - self.overlap - 2*self.margin)/self.scale

@dataclass
class Point:
    x: float = 0.0
    y: float = 0.0

    def offset(self, offset: 'Point'):
        self.x = self.x - offset.x
        self.y = self.y - offset.y

@dataclass
class RectXY:
    bl: Point
    tr: Point

    def offset(self, offset: Point):
        self.bl.offset(offset)
        self.tr.offset(offset)

@dataclass
class Line:
    start: Point
    end: Point

    @staticmethod
    def from_dxf(e):
        return Line(start=Point(e.start[0], e.start[1]), end=Point(e.end[0], e.end[1]))

    def bounds(self) -> RectXY:
        return RectXY(Point(min(self.start.x, self.end.x),
                            min(self.start.y, self.end.y)),
                      Point(max(self.start.x, self.end.x), 
                            max(self.start.y, self.end.y)))

    def offset(self, offset: Point):
        self.start.offset(offset)
        self.end.offset(offset)
        
@dataclass
class Arc:
    center: Point
    r: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0

    @staticmethod
    def from_dxf(e):
        return Arc(center= Point(e.center[0], e.center[1]), r=e.radius,
                   start_angle=e.start_angle, end_angle=e.end_angle)

    def bounds(self):
        cx, cy = self.center.x, self.center.y
        max_x = -math.inf
        min_x = math.inf
        max_y = -math.inf
        min_y = math.inf

        angles = [self.start_angle, self.end_angle]
        wrap = self.start_angle > self.end_angle
        for extra_angle in [0, 90, 180, 270]:
            if ((self.start_angle <= extra_angle <= self.end_angle) or 
                (wrap and extra_angle <= self.end_angle) or
                (wrap and self.start_angle <= extra_angle)):
                angles.append(extra_angle)

        # print(f'{angles=}')
        for angle in angles:
            x = cx + self.r*math.cos(angle * math.pi/180)
            y = cy + self.r*math.sin(angle * math.pi/180)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
            min_x = min(min_x, x)
            min_y = min(min_y, y)

        return RectXY(Point(min_x, min_y), Point(max_x, max_y))

    def offset(self, offset: Point):
        self.center.offset(offset)

def update_bounds(bb1: RectXY, bb2: RectXY):
    bl = Point(min(bb1.bl.x, bb2.bl.x), min(bb1.bl.y, bb2.bl.y))
    tr = Point(max(bb1.tr.x, bb2.tr.x), max(bb1.tr.y, bb2.tr.y))
    return RectXY(bl, tr)

def draw_page(params: Params, entities, pdf, bb: Point, offset: Point):
    ox = offset.x
    oy = offset.y

    def draw_line(x1, y1, x2, y2):
        pdf.line(x1=params.scale*(x1+ox)+params.margin, 
                 y1=params.page_h-params.scale*(y1+oy)-params.margin,
                 x2=params.scale*(x2+ox)+params.margin,
                 y2=params.page_h-params.scale*(y2+oy)-params.margin)

    def draw_arc(e: Arc):
        cx = params.scale*(e.center.x+ox)
        cy = params.scale*(e.center.y+oy)
        r = params.scale*e.r
        pdf.arc(x=cx-r+params.margin, y=params.page_h-(cy+r)-params.margin, a=2*r, b=2*r,
                end_angle=360-e.start_angle, start_angle=360-e.end_angle)

    pdf.set_draw_color(200)

    bb_r = min(bb.x, bb.y)
    for i in range(math.ceil(params.scale*(bb.x+2*bb_r))+1):
        x1 = i/params.scale - bb_r
        x2 = x1 + bb_r
        draw_line(x1, 0, x2, bb_r)
        draw_line(x1, bb_r, x2, 0)

    pdf.set_draw_color(r=0, g=0, b=0)
    for e in entities:
        if isinstance(e, Line):
            draw_line(e.start.x, e.start.y, e.end.x, e.end.y)
        if isinstance(e, Arc):
            draw_arc(e)

    num_cutsx = math.ceil(bb.x/params.cutx)
    num_cutsy = math.ceil(bb.y/params.cuty)
    for i in range(num_cutsx+1):
        for j in range(num_cutsy+1):
            x = i*params.cutx
            y = j*params.cuty
            draw_line(x-0.25/params.scale, y, x+0.25/params.scale, y)
            draw_line(x, y-0.25/params.scale, x, y+0.25/params.scale)

def main():
    parser = argparse.ArgumentParser(description='Convert DXF to PDF')
    parser.add_argument('dxf', type=str, help='DXF file to convert')
    parser.add_argument('--pdf', required=True, type=str, help='PDF file to output')
    parser.add_argument('--scale', required=True, type=float, help='Scale factor')
    parser.add_argument('--overlap', type=float, default=0.5, help='Overlap factor')

    args = parser.parse_args()

    dxf = dxfgrabber.readfile(args.dxf)
    bb = RectXY(bl = Point(math.inf, math.inf), 
                tr = Point(-math.inf, -math.inf))
    entities = []
    for e in dxf.entities:
        if e.dxftype == 'LINE':
            entity = Line.from_dxf(e)
            entities.append(Line.from_dxf(e))
        elif e.dxftype == 'ARC':
            entity = Arc.from_dxf(e)
        else:
            raise ValueError(f"Unsupported entity type: {e.dxftype}")

        entities.append(entity)
        bounds = entity.bounds()
        # print(f'{entity} -> {bounds.bl.y} -> {bounds.tr.y}')
        bb = update_bounds(bb, bounds)

    for e in entities:
        e.offset(bb.bl)

    bb.offset(replace(bb.bl))
    print(f"Bounding box: {bb}")
    orientation = "landscape" if bb.tr.x > bb.tr.y else "portrait"

    pdf = FPDF(orientation=orientation, unit="in", format="letter")
    pdf.set_line_width(0.5/25.4)
    pdf.set_font("Helvetica", "B", 8) 

    params = Params(overlap=args.overlap, scale=args.scale, orientation=orientation)
    print(params)
    print(f'''Page size: {params.page_w} x {params.page_h}''')

    num_cutsx = math.ceil(bb.tr.x/params.cutx)
    num_cutsy = math.ceil(bb.tr.y/params.cuty)
    print(f"cutx: {params.cutx}, cuty: {params.cuty}")
    print(f"Cutting into {num_cutsx} x {num_cutsy} pages")
    for i in range(num_cutsx):
        for j in range(num_cutsy):
            x = i*params.cutx
            y = j*params.cuty

            pdf.add_page()
            tol = 0.5/25.4
            with pdf.rect_clip(params.margin-tol, params.margin-tol, 
                               params.page_w+2*tol - 2*params.margin, 
                               params.page_h+2*tol - 2*params.margin):
                draw_page(params, entities, pdf, bb.tr, Point(-x, -y))

            pdf.text(0.3, 0.4, text=f'({i}, {j})')

    pdf.output(args.pdf)


# arc = Arc(center=Point(x=2.999999999999995, y=39.27596614930593), r=30.7812500000007, start_angle=270.0, end_angle=273.7253958543048)
# print(arc.bounds())

main()
