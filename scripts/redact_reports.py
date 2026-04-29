import re
from pathlib import Path


def anonymize_content(content):
    # 1. Specific headers
    content = re.sub(r"Hostname:\s+[^\n]+", "Hostname:  [ANONYMIZED]", content)
    content = re.sub(r"Binary:\s+[^\n]+", "Binary:    [ANONYMIZED]", content)
    content = re.sub(r"Worker ID:\s+[^\n]+", "Worker ID: [ANONYMIZED]", content)
    content = re.sub(r"Azure Region:\s+[^\n]+", "Azure Region: [ANONYMIZED]", content)

    # 2. Absolute paths (macOS and Linux/Windows)
    content = re.sub(
        r"(/Users/[^/\s]+|/home/[^/\s]+|[A-Z]:\\Users\\[^\\\s]+)", "[HOME]", content
    )

    # 3. Path markers
    content = re.sub(r"✅\s+/[^\n\x1b]+", "✅  [PATH]", content)
    content = re.sub(r"⚠️\s+/[^\n\x1b]+", "⚠️  [PATH]", content)
    content = re.sub(r"❌\s+/[^\n\x1b]+", "❌  [PATH]", content)

    # 4. Windows Specific binary paths
    content = re.sub(r"✅\s+[A-Z]:\\[^\n\x1b]+", "✅  [PATH]", content)
    content = re.sub(r"⚠️\s+[A-Z]:\\[^\n\x1b]+", "⚠️  [PATH]", content)
    content = re.sub(r"❌\s+[A-Z]:\\[^\n\x1b]+", "❌  [PATH]", content)

    return content


for p in Path("references/verification-results").rglob("verify-*.txt"):
    if not p.is_file():
        continue
    raw = p.read_text(errors="ignore")
    clean = anonymize_content(raw)
    p.write_text(clean)
