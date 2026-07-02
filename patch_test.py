with open("ldm_core/tests/test_license.py") as f:
    content = f.read()

content = content.replace(
    "self.assertTrue(ok)",
    'print(f"DEBUG: dir contents: {list(self.common_dir.iterdir())}"); print(f"DEBUG: found: {self.manager.find_license(self.paths)}"); self.assertTrue(ok)',
)

with open("ldm_core/tests/test_license.py", "w") as f:
    f.write(content)
