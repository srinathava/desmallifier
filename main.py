import ezdxf
from fpdf import FPDF
import math
from dataclasses import dataclass, replace
import argparse
import numpy as np
import os
import tempfile
import PyPDF2
from pdf2image import convert_from_path
from PIL import Image, ImageStat

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

@dataclass
class Ellipse:
    center: Point
    major_axis: Point  # Vector from center to major axis endpoint
    ratio: float = 0.0  # Ratio of minor axis to major axis
    start_param: float = 0.0  # Start parameter in radians
    end_param: float = 0.0  # End parameter in radians
    
    @staticmethod
    def from_dxf(e):
        center = Point(e.center[0], e.center[1])
        # Major axis is stored as a vector from center
        major_axis = Point(e.major_axis[0], e.major_axis[1])
        return Ellipse(center=center,
                      major_axis=major_axis,
                      ratio=e.ratio,
                      start_param=e.start_param,
                      end_param=e.end_param)
    
    def bounds(self, num_points=1000):
        # Extract ellipse data
        cx, cy = self.center.x, self.center.y
        dx, dy = self.major_axis.x, self.major_axis.y
        a = np.hypot(dx, dy)
        b = a * self.ratio
        theta = np.arctan2(dy, dx)

        # Normalize angles
        start = self.start_param % (2 * np.pi)
        end = self.end_param % (2 * np.pi)
        if end <= start:
            end += 2 * np.pi

        # Sample points
        t = np.linspace(start, end, num_points)
        x_ = a * np.cos(t)
        y_ = b * np.sin(t)

        # Rotate points by θ and shift to center
        cos_theta = np.cos(theta)
        sin_theta = np.sin(theta)
        x = x_ * cos_theta - y_ * sin_theta + cx
        y = x_ * sin_theta + y_ * cos_theta + cy

        # Compute bounding box
        min_x, max_x = np.min(x).item(), np.max(x).item()
        min_y, max_y = np.min(y).item(), np.max(y).item()

        return RectXY(Point(min_x, min_y), Point(max_x, max_y))
        
    def offset(self, offset: Point):
        self.center.offset(offset)

def update_bounds(bb1: RectXY, bb2: RectXY):
    bl = Point(min(bb1.bl.x, bb2.bl.x), min(bb1.bl.y, bb2.bl.y))
    tr = Point(max(bb1.tr.x, bb2.tr.x), max(bb1.tr.y, bb2.tr.y))
    return RectXY(bl, tr)

class PDFDrawer:
    """Class for drawing geometric entities on a PDF."""
    
    def __init__(self, params: Params, pdf, offset: Point):
        """
        Initialize the PDFDrawer with parameters needed for drawing.
        
        Args:
            params: Drawing parameters (scale, page dimensions, margins)
            pdf: FPDF object to draw on
            offset: Offset point for drawing coordinates
        """
        self.params = params
        self.pdf = pdf
        self.ox = offset.x
        self.oy = offset.y
    
    def draw_line(self, x1, y1, x2, y2):
        """Draw a line on the PDF with appropriate scaling and offset."""
        self.pdf.line(
            x1=self.params.scale*(x1+self.ox)+self.params.margin,
            y1=self.params.page_h-self.params.scale*(y1+self.oy)-self.params.margin,
            x2=self.params.scale*(x2+self.ox)+self.params.margin,
            y2=self.params.page_h-self.params.scale*(y2+self.oy)-self.params.margin
        )

    def draw_arc(self, e: Arc):
        """Draw an arc on the PDF with appropriate scaling and offset."""
        cx = self.params.scale*(e.center.x+self.ox)
        cy = self.params.scale*(e.center.y+self.oy)
        r = self.params.scale*e.r
        self.pdf.arc(
            x=cx-r+self.params.margin,
            y=self.params.page_h-(cy+r)-self.params.margin,
            a=2*r,
            b=2*r,
            end_angle=360-e.start_angle,
            start_angle=360-e.end_angle
        )
    
    def draw_ellipse(self, e: Ellipse):
        """Draw an ellipse on the PDF with appropriate scaling and offset."""
        # Get center point in PDF coordinates
        cx = self.params.scale*(e.center.x+self.ox) + self.params.margin
        cy = self.params.page_h - self.params.scale*(e.center.y+self.oy) - self.params.margin
        
        # Calculate major and minor axis lengths
        major_length = self.params.scale * math.sqrt(e.major_axis.x**2 + e.major_axis.y**2)
        minor_length = major_length * e.ratio
        
        # Calculate rotation angle of the ellipse in degrees
        rotation_deg = math.atan2(e.major_axis.y, e.major_axis.x) * 180 / math.pi
        
        # Convert parameters from radians to degrees
        start_angle = math.degrees(e.start_param)
        end_angle = math.degrees(e.end_param)
        
        # Use arc for both full and partial ellipses
        self.pdf.arc(
            x=cx-major_length,
            y=cy-minor_length,
            a=2*major_length,
            b=2*minor_length,
            start_angle=start_angle,
            end_angle=end_angle,
            inclination=rotation_deg,
            clockwise=True
        )
    
    def draw_entity(self, e):
        """Draw an entity based on its type."""
        if isinstance(e, Line):
            self.draw_line(e.start.x, e.start.y, e.end.x, e.end.y)
        elif isinstance(e, Arc):
            self.draw_arc(e)
        elif isinstance(e, Ellipse):
            self.draw_ellipse(e)


def draw_page(params: Params, entities, pdf, offset: Point):
    """Draw entities on a PDF page using PDFDrawer."""
    drawer = PDFDrawer(params, pdf, offset)
    pdf.set_draw_color(r=0, g=0, b=0)
    for e in entities:
        drawer.draw_entity(e)

def draw_grid_markers(pdf, params: Params, bb: Point, offset: Point):
    drawer = PDFDrawer(params, pdf, offset)
    pdf.set_draw_color(200)
    bb_r = max(bb.x, bb.y)
    for i in range(math.ceil(params.scale*(bb.x+2*bb_r))+1):
        x1 = i/params.scale - bb_r
        x2 = x1 + bb_r
        drawer.draw_line(x1, 0, x2, bb_r)
        drawer.draw_line(x1, bb_r, x2, 0)
    
    num_cutsx = math.ceil(bb.x/params.cutx)
    num_cutsy = math.ceil(bb.y/params.cuty)
    for i in range(num_cutsx+1):
        for j in range(num_cutsy+1):
            x = i*params.cutx
            y = j*params.cuty
            drawer.draw_line(x-0.25/params.scale, y, x+0.25/params.scale, y)
            drawer.draw_line(x, y-0.25/params.scale, x, y+0.25/params.scale)


# Function to check if a page is visually empty
def analyze_pdf_for_empty_pages(pdf_path):
    """Analyze a PDF and return a list of page indices that have visible content."""
    # Create a temporary directory for image processing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Convert PDF pages to images for visual analysis
        images = convert_from_path(pdf_path, dpi=72, output_folder=temp_dir)
        
        non_empty_pages = []
        
        for page_idx, img in enumerate(images):
            # Convert to grayscale for simpler analysis
            gray_img = img.convert('L')
            
            # Get image statistics
            stats = ImageStat.Stat(gray_img)
            
            # Check if the image is mostly white (255) with very little variation
            mean_val = stats.mean[0]
            stddev = stats.stddev[0]
            
            # Count non-white pixels (threshold slightly below 255 to account for compression artifacts)
            non_white_count = 0
            for pixel in gray_img.getdata():
                if pixel < 250:  # Threshold for considering a pixel non-white
                    non_white_count += 1
            
            # Calculate percentage of non-white pixels
            total_pixels = gray_img.width * gray_img.height
            non_white_percentage = (non_white_count / total_pixels) * 100
            
            is_empty = non_white_percentage < 0.1
            
            if not is_empty:
                non_empty_pages.append(page_idx)
        
        return non_empty_pages


def main():
    parser = argparse.ArgumentParser(description='Convert DXF to PDF')
    parser.add_argument('dxf', type=str, help='DXF file to convert')
    parser.add_argument('--pdf', required=True, type=str, help='PDF file to output')
    parser.add_argument('--scale', required=True, type=float, help='Scale factor')
    parser.add_argument('--overlap', type=float, default=0.5, help='Overlap factor')
    parser.add_argument('--debug', action='store_true', help='Debug mode with debugpy')

    args = parser.parse_args()
    if args.debug:
        import debugpy
        print("Waiting for debugger to attach...")
        debugpy.listen(5678)
        debugpy.wait_for_client()

    dxf = ezdxf.readfile(args.dxf)
    bb = RectXY(bl = Point(math.inf, math.inf),
                tr = Point(-math.inf, -math.inf))
    entities = []
    def add_entity(e):
        nonlocal bb, entities

        entities.append(e)
        bounds = e.bounds()
        # print(f'{entity} -> {bounds.bl.y} -> {bounds.tr.y}')
        bb = update_bounds(bb, bounds)

    for e in dxf.modelspace():
        dxftype = e.dxftype()
        if dxftype == 'LINE':
            add_entity(Line.from_dxf(e.dxf))
        elif dxftype == 'ARC':
            add_entity(Arc.from_dxf(e.dxf))
        elif dxftype == 'ELLIPSE':
            add_entity(Ellipse.from_dxf(e.dxf))
        elif dxftype == 'SPLINE':
            points = list(e.construction_tool().flattening(distance=0.001))
            for (p1, p2) in zip(points[:-1], points[1:]):
                add_entity(Line(start=Point(p1[0], p1[1]), end=Point(p2[0], p2[1])))

        elif dxftype == 'MTEXT':
            print(f"Skipping MTEXT: {e.text}")
            continue
        else:
            raise ValueError(f"Unsupported entity type: {dxftype}")
        

    for e in entities:
        e.offset(bb.bl)

    bb.offset(replace(bb.bl))
    print(f"Bounding box: {bb}")
    orientation = "landscape" if bb.tr.x > bb.tr.y else "portrait"

    def create_pdf():
        pdf = FPDF(orientation=orientation, unit="in", format="letter")
        pdf.set_line_width(0.5/25.4)
        pdf.set_font("Helvetica", "B", 8)
        return pdf
    
    pdf = create_pdf()
    grid_pdf = create_pdf()

    params = Params(overlap=args.overlap, scale=args.scale, orientation=orientation)
    print(params)
    print(f'''Page size: {params.page_w} x {params.page_h}''')

    num_cutsx = math.ceil(bb.tr.x/params.cutx)
    num_cutsy = math.ceil(bb.tr.y/params.cuty)
    print(f"cutx: {params.cutx}, cuty: {params.cuty}")
    print(f"Cutting into {num_cutsx} x {num_cutsy} pages")
    
    # Generate PDF with all pages
    for i in range(num_cutsx):
        for j in range(num_cutsy):
            x = i*params.cutx
            y = j*params.cuty

            pdf.add_page()
            tol = 0.5/25.4
            with pdf.rect_clip(params.margin-tol, params.margin-tol,
                               params.page_w+2*tol - 2*params.margin,
                               params.page_h+2*tol - 2*params.margin):
                draw_page(params, entities, pdf, Point(-x, -y))

            grid_pdf.add_page()
            with grid_pdf.rect_clip(params.margin-tol, params.margin-tol,
                                     params.page_w+2*tol - 2*params.margin,
                                     params.page_h+2*tol - 2*params.margin):
                draw_grid_markers(grid_pdf, params, bb.tr, Point(-x, -y))
                grid_pdf.text(0.3, 0.4, text=f'({i}, {j})')
    
    # Save to a temporary file
    temp_pdf_path = args.pdf.replace(".pdf", ".temp.pdf")
    pdf.output(temp_pdf_path)

    grid_pdf_path = args.pdf.replace(".pdf", ".grid.pdf")
    grid_pdf.output(grid_pdf_path)
    
    # Open the temporary PDF and filter out empty pages
    with open(temp_pdf_path, 'rb') as file, open(grid_pdf_path, 'rb') as grid_file:
        reader = PyPDF2.PdfReader(file)
        grid_reader = PyPDF2.PdfReader(grid_file)
        writer = PyPDF2.PdfWriter()
        
        # Analyze the PDF to find non-empty pages
        print("Analyzing PDF pages for visible content...")
        non_empty_pages = analyze_pdf_for_empty_pages(temp_pdf_path)
        
        # Add only non-empty pages to the new PDF
        pages_kept = 0
        pages_removed = 0
        
        for i, page_pair in enumerate(zip(reader.pages, grid_reader.pages)):
            page, grid_page = page_pair
            if i in non_empty_pages:
                grid_page.merge_page(page)
                writer.add_page(grid_page)
                pages_kept += 1
            else:
                pages_removed += 1
        
        # Write the filtered PDF to the final output file
        with open(args.pdf, 'wb') as output_file:
            writer.write(output_file)
    
    # Remove the temporary file
    os.remove(temp_pdf_path)
    
    print(f"Kept {pages_kept} pages, removed {pages_removed} empty pages")


if __name__ == "__main__":
    main()
