import os
import glob
import re

directory = r"f:/pos/setup/templates/setup"
files = [f for f in glob.glob(os.path.join(directory, "*.html")) if not f.endswith("setup.html")]

head_replace = """    <style>
        :root {
            --bg-color: #fafafa; --panel-bg: #ffffff; --text-main: #111111; --text-muted: #737373;
            --border-color: #eaeaea; --accent-gold: #c5a059; --transition-smooth: all 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }
        body.dark {
            --bg-color: #0a0a0a; --panel-bg: #111111; --text-main: #f5f5f5; --text-muted: #888888;
            --border-color: #262626; --accent-gold: #d4af37;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: var(--bg-color); color: var(--text-main); font-family: 'Inter', sans-serif; min-height: 100vh; transition: var(--transition-smooth); -webkit-font-smoothing: antialiased; }
        
        /* HEADER */
        .pos-header { padding: 1rem 1.5rem; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; background: var(--bg-color); }
        .brand-title { font-size: 1.3rem; margin: 0; color: var(--text-main); display: flex; align-items: center; gap: 10px; font-family: 'Playfair Display', serif; font-weight: 700; }
        .brand-title i { color: var(--accent-gold); }
        .btn-luxury { border: 1px solid var(--border-color); background: transparent; color: var(--text-main); border-radius: 0; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 1px; font-weight: 500; padding: 0.6rem 1rem; transition: var(--transition-smooth); text-decoration: none; }
        .btn-luxury:hover { border-color: var(--text-main); color: var(--bg-color); background: var(--text-main); }
        .btn-icon { border: none; background: transparent; color: var(--text-main); font-size: 1.25rem; padding: 0.5rem; cursor: pointer; }

        .page-eyebrow { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 4px; color: var(--accent-gold); margin-bottom: 0.5rem; }
        .page-title { font-family: 'Playfair Display', serif; font-size: 1.8rem; font-weight: 700; }
        .divider { width: 40px; height: 1px; background: var(--accent-gold); margin: 1.2rem 0; }
        .page-sub { color: var(--text-muted); font-size: 0.8rem; line-height: 1.6; margin-bottom: 2rem; }

        .form-label, .section-label { display: block; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 2px; color: var(--text-muted); font-weight: 600; margin-bottom: 0.5rem; }
        .form-control { width: 100%; background: transparent; border: none; border-bottom: 1px solid var(--border-color); color: var(--text-main); padding: 0.6rem 0; font-size: 1.2rem; font-weight: 600; outline: none; margin-bottom: 1.5rem; font-family: inherit; }
        .form-control:focus { border-bottom-color: var(--accent-gold); box-shadow: none; background: transparent; color: var(--text-main); }
        .form-hint, .empty-state { font-size: 0.7rem; color: var(--text-muted); margin-bottom: 1.5rem; }
        .btn-add { width: 100%; padding: 0.9rem; background: var(--accent-gold); border: none; color: #fff; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 3px; font-weight: 700; cursor: pointer; transition: background 0.2s; }
        .btn-add:hover { opacity: 0.9; }

        .alert { padding: 0.8rem 1.2rem; margin-bottom: 1.5rem; font-size: 0.8rem; border-left: 3px solid var(--accent-gold); background: rgba(197,160,89,0.08); }
        .alert-warning { border-left-color: #ef4444; background: rgba(239,68,68,0.08); }
"""

header_replace = """<header class="pos-header">
    <h1 class="brand-title"><i class="bi bi-asterisk"></i> POS Setup</h1>
    <div class="d-flex align-items-center gap-2">
        <a href="/setup/" class="btn-luxury d-none d-sm-inline-flex"><i class="bi bi-gear me-1"></i> Setup Area</a>
        <button class="btn-icon ms-1" onclick="toggleDark()" title="Toggle Theme">
            <i class="bi bi-moon" id="themeIcon"></i>
        </button>
    </div>
</header>"""

js_replace = """
<script>
    let isDark = localStorage.getItem("dark") === "true";
    function updateIcons() { let icon = document.getElementById("themeIcon"); if(isDark) { document.body.classList.add("dark"); icon.classList.replace("bi-moon", "bi-sun"); } else { document.body.classList.remove("dark"); icon.classList.replace("bi-sun", "bi-moon"); } }
    updateIcons();
    function toggleDark() { isDark = !isDark; localStorage.setItem("dark", isDark); updateIcons(); }
</script>
</body>
"""

for filepath in files:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Replace everything between <style> and /* inside style with head_replace
    content = re.sub(r'<style>[\s\S]*?(?=\s*(?:\.page-body|/\*))', head_replace, content)
    
    # 2. Add bootstrap to head if not present
    if "bootstrap.min.css" not in content:
        content = content.replace("<style>", '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">\n    <style>')
    
    # 3. Handle old CSS var names
    content = content.replace("var(--panel)", "var(--panel-bg)")
    content = content.replace("var(--border)", "var(--border-color)")
    content = content.replace("var(--gold)", "var(--accent-gold)")
    content = content.replace("var(--muted)", "var(--text-muted)")
    content = content.replace("var(--text)", "var(--text-main)")
    content = content.replace("var(--bg)", "var(--bg-color)")

    # 4. Replace Header
    content = re.sub(r'<header class="site-header">[\s\S]*?</header>', header_replace, content)

    # 5. Replace </body>
    content = content.replace("</body>", js_replace)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

print("Updated setup files successfully!")
