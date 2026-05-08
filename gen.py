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
