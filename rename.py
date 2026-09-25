import os

def replace_in_file(file_path, old, new):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if old in content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content.replace(old, new))
        print(f"Updated {file_path}")

for root, dirs, files in os.walk('.'):
    if '.git' in root or '.terraform' in root: continue
    for file in files:
        if file.endswith('.md') or file.endswith('.tf') or file.endswith('.py') or file.endswith('.example') or file.endswith('.json'):
            path = os.path.join(root, file)
            # Update README title
            replace_in_file(path, '# Recovery-Engine-AWS: Multi-Region Disaster Recovery', '# Recovery-Engine-AWS: Multi-Region Disaster Recovery')
            
            # Update terraform variable defaults and python fallbacks
            replace_in_file(path, 'default = "recovery-engine"', 'default = "recovery-engine"')
            replace_in_file(path, 'default = "recovery-engine"', 'default = "recovery-engine"')
            
            # String replacements
            replace_in_file(path, '"recovery-engine"', '"recovery-engine"')
            replace_in_file(path, 'recovery-engine-aws', 'recovery-engine-aws')
