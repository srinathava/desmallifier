# Desmallifier

A simple python script which scales up and splits a DXF file across
multiple pages to enable printing to a real-world 1:1 scale. Also overlays
a diagonal grid to make alignment easier. This is mostly meant as an aid to
template cutting for woodworking etc.

```bash
usage: main.py [-h] --pdf PDF --scale SCALE [--overlap OVERLAP] dxf

Convert DXF to PDF

positional arguments:
  dxf                DXF file to convert

options:
  -h, --help         show this help message and exit
  --pdf PDF          PDF file to output
  --scale SCALE      Scale factor
  --overlap OVERLAP  Overlap factor
```

As an example, `test.pdf` was created from `test.dxf` using the following
command:

```
python main.py test.dxf --pdf /tmp/test.pdf --scale=2.5 --overlap=1.0
```

## Installation

This script requires two python packages `dxfgrabber` (for reading DXF
files) and `fpdf2` (for writing PDF files). Install them using `conda`,
`pip` or your favorite package manager.
