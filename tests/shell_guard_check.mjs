import assert from 'node:assert/strict';
import { explicitApproval } from '../edge_agent/shell_guard.mjs';

const destructive = ['Remove-Item -Recurse -Force data', 'rm -rf build', 'del /q *.tmp', 'cd 資料; ri cache.db',
  '(Remove-Item a.txt)', 'git reset --hard HEAD', 'git clean -fdx', 'git checkout -- src', 'git push --force',
  "[IO.File]::Delete('a')", 'Clear-Content log.txt', 'Get-ChildItem *.tmp | Remove-Item'];
const protectedFiles = ['Get-Content .env', "Set-Content settings.json '{}'", 'type .env.local',
  'Copy-Item x personas.json', 'notepad "C:\\Users\\me\\character_state.json"'];
const ordinary = ['python -m pytest -q', 'Get-ChildItem -Recurse', 'git status', 'git diff', 'echo format_string',
  'Get-Content README.md', 'Move-Item a.jpg 圖片/', 'npm run lint', 'Get-Content .envrc', "Write-Output 'rm me'",
  'python manage.py remove_user', 'Select-String -Pattern delete src/*.py'];
for (const command of destructive) assert.equal(explicitApproval(command), 'destructive', command);
for (const command of protectedFiles) assert.equal(explicitApproval(command), 'protected', command);
for (const command of ordinary) assert.equal(explicitApproval(command), '', command);
console.log('shell guard ok');
