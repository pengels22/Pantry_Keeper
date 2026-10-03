"""Bundle Safari extension source and the Mac packaging script without app secrets."""
import json
import shutil
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
output = root / 'dist' / 'Pantry-Keeper-Safari-source.zip'
output.parent.mkdir(exist_ok=True)
files = sorted((root / 'browser_extension').rglob('*'))
files.append(root / 'scripts' / 'package-safari.sh')
with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
    for file in files:
        if file.is_file():
            archive.write(file, Path('Pantry-Keeper-Safari') / file.relative_to(root))
print(output)

version = json.loads((root / 'browser_extension' / 'manifest.json').read_text())['version']
versioned = output.with_name(f'Pantry-Keeper-Safari-v{version}-source.zip')
shutil.copyfile(output, versioned)
print(versioned)
