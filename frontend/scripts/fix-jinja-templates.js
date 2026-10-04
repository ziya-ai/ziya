// Add the Jinja block that loads formatter scripts to the built index.html.
//
// This lives in a file rather than inline in package.json: on Windows npm runs
// scripts with cmd.exe, which treats the "|" in "formatter_script|safe" as a
// pipe once the escaped quotes around it have toggled its quoting.
const fs = require('fs');
const path = require('path');

const f = path.join(__dirname, '..', '..', 'templates', 'index.html');
let h = fs.readFileSync(f, 'utf8');
h = h.replace(/!function\(\)\{try\{formatter_script,safe\}catch\(r\)\{console\.error\(.*?\)\}\}\(\)/g, '');
h = h.replace(
  /<\/body>/,
  '{% if formatter_scripts %}{% for formatter_script in formatter_scripts %}' +
    '<script src="{{ formatter_script|safe }}" onerror="console.warn(\'Formatter load error\');"></script>' +
    '{% endfor %}{% endif %}</body>'
);
fs.writeFileSync(f, h);
