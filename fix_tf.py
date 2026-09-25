import os, re

def fix_line(line):
    # match: variable "name" { type = string default = "x" }
    m = re.match(r'^(\s*variable\s+"[^"]+"\s*)\{\s*(.*?)\s*\}\s*$', line)
    if not m:
        return line
    prefix = m.group(1)
    inner = m.group(2).strip()
    
    # insert newlines before known keywords
    inner = re.sub(r'\b(type\s*=)', r'\n  \1', inner)
    inner = re.sub(r'\b(default\s*=)', r'\n  \1', inner)
    inner = re.sub(r'\b(description\s*=)', r'\n  \1', inner)
    inner = re.sub(r'\b(sensitive\s*=)', r'\n  \1', inner)
    
    return f'{prefix}{{{inner}\n}}\n'

def fix_terraform():
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.tf'):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                new_lines = []
                for line in lines:
                    new_line = fix_line(line)
                    # Fix monitoring dashboard syntax
                    if 'monitoring' in path and 'main.tf' in path:
                        new_line = new_line.replace('x      = 0; y = 0; width = 12; height = 6', 'x = 0, y = 0, width = 12, height = 6')
                        new_line = new_line.replace('x      = 12; y = 0; width = 12; height = 6', 'x = 12, y = 0, width = 12, height = 6')
                        new_line = new_line.replace('x      = 0; y = 6; width = 8; height = 6', 'x = 0, y = 6, width = 8, height = 6')
                        new_line = new_line.replace('x      = 8; y = 6; width = 8; height = 6', 'x = 8, y = 6, width = 8, height = 6')
                        new_line = new_line.replace('x      = 16; y = 6; width = 8; height = 6', 'x = 16, y = 6, width = 8, height = 6')
                    new_lines.append(new_line)
                
                content = "".join(lines)
                new_content = "".join(new_lines)
                if content != new_content:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    print(f'Fixed {path}')

if __name__ == "__main__":
    fix_terraform()
