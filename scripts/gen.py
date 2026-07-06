"""
One-off script: renders a marketing brochure PDF from `brochure.html` using
WeasyPrint. Not wired into any Django app or view — run manually when needed.

Currently non-functional as-is: expects a `brochure.html` file in this same
directory (scripts/), which does not exist in the repo. Create one before
running.

Run: python scripts/gen.py
"""
from weasyprint import HTML
from pathlib import Path

# ---------------------------------------------------
# FILE PATHS
# ---------------------------------------------------

BASE_DIR = Path(__file__).parent

html_file = BASE_DIR / "brochure.html"
pdf_file = BASE_DIR / "rasova_brochure.pdf"

# ---------------------------------------------------
# READ HTML
# ---------------------------------------------------

html_content = html_file.read_text(
    encoding="utf-8"
)

# ---------------------------------------------------
# GENERATE PDF
# ---------------------------------------------------

HTML(
    string=html_content,
    base_url=str(BASE_DIR)
).write_pdf(
    str(pdf_file)
)

print("PDF generated successfully!")
print(f"Saved at: {pdf_file}")
