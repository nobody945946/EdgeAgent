// Shell commands that touch user-owned configuration or delete things always
// wait for an explicit, single approval: neither "allow" nor an earlier
// "allow this turn" covers them (the bench saw a 4B model rewrite .env and
// settings.json through PowerShell and delete a database named cache.db).
const PROTECTED_NAME = /(?:^|[\\/\s'"`(=])(?:\.env(?:\.[\w.-]+)?|settings\.json|personas\.json|character_state\.json)(?=$|[\s'"`;|)&,])/i;
const DESTRUCTIVE = /(?:^|[\s;|&({])(?:Remove-Item|rm|rmdir|rd|del|erase|ri|Clear-Content|Clear-RecycleBin|Format-Volume|format|Remove-ItemProperty)(?=$|[\s;|&)])|\bgit\s+(?:reset\s+--hard|clean\b|checkout\s+--|restore\b|push\s+(?:-f|--force))|\[(?:System\.)?IO\.(?:File|Directory)\]::Delete/i;
// The owner's memory (its AGENTS.md in Pi's agentDir, .runtime/pi-state)
// takes what the owner allows each time, through edit or a command alike.
const MEMORY = /pi-state/i;
export function explicitApproval(value) {
  const command = String(value ?? '');
  if (PROTECTED_NAME.test(command)) return 'protected';
  if (DESTRUCTIVE.test(command)) return 'destructive';
  if (MEMORY.test(command)) return 'memory';
  return '';
}
