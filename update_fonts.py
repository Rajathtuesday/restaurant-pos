import glob
import re

html_files = glob.glob('f:/pos/**/*.html', recursive=True)
new_link = 'https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=DM+Serif+Display:ital@0;1&family=Space+Mono&display=swap'

updated = 0
for path in html_files:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        new_content = re.sub(r'https://fonts\.googleapis\.com/css2\?[^"]+', new_link, content)
        new_content = new_content.replace("'Playfair Display'", "'DM Serif Display'").replace("Playfair Display", "DM Serif Display")
        new_content = new_content.replace("'Inter'", "'Outfit'").replace("Inter, sans-serif", "Outfit, sans-serif")
        
        if content != new_content:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print('Updated', path)
            updated += 1
    except Exception as e:
        print(f"Failed {path}: {e}")

print(f"Done. Updated {updated} files.")
