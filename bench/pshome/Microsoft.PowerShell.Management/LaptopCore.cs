// Get-Process, Get-PSDrive and Get-ComputerInfo answering with the laptop, as
// cmdlets of Microsoft.PowerShell.Management, where Windows has them: a
// function from any other module is not what the product's read proof
// (edge_agent/permissions.py) accepts, and a module of that name on
// PSModulePath loses to PowerShell's own. bench/run.py compiles this into
// LaptopCore.dll with the sandbox's PowerShell and mounts the manifest next
// to it over the sandbox's own; the work is the Get-Laptop* functions in
// bench/windows/Laptop.ps1. Without the laptop's facts (a sandbox not dressed
// as the laptop) each runs PowerShell's own cmdlet.
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Management.Automation;

namespace LaptopCore
{
    internal static class Laptop
    {
        private const string Script = "/opt/bench/windows/Laptop.ps1";

        private static string Facts()
        {
            return System.Environment.GetEnvironmentVariable("LAPTOP_FACTS") ?? "/tmp/.laptop/machine.json";
        }

        // The laptop's function when the facts are there, else PowerShell's own
        // cmdlet (`own`, by type name); objects are written, error records written as errors.
        public static void Answer(PSCmdlet cmdlet, string function, string own)
        {
            var bound = new Hashtable();
            foreach (KeyValuePair<string, object> pair in cmdlet.MyInvocation.BoundParameters)
            {
                bound[pair.Key] = pair.Value;
            }
            ScriptBlock block;
            if (own == null && !File.Exists(Facts()))
            {
                cmdlet.ThrowTerminatingError(new ErrorRecord(
                    new PSNotSupportedException(cmdlet.MyInvocation.MyCommand.Name + " answers only in a sandbox dressed as the laptop."),
                    "NoLaptopFacts", ErrorCategory.NotImplemented, null));
            }
            if (File.Exists(Facts()))
            {
                block = cmdlet.InvokeCommand.NewScriptBlock("param($bound) . '" + Script + "'; " + function + " @bound");
            }
            else
            {
                block = cmdlet.InvokeCommand.NewScriptBlock(
                    "param($bound, $own, $name) & ([System.Management.Automation.CmdletInfo]::new($name, ($own -as [type]))) @bound " +
                    "-ErrorAction SilentlyContinue -ErrorVariable failed; $failed");
            }
            foreach (PSObject item in block.Invoke(bound, own, cmdlet.MyInvocation.MyCommand.Name))
            {
                if (item != null && item.BaseObject is ErrorRecord error)
                {
                    cmdlet.WriteError(error);
                }
                else
                {
                    cmdlet.WriteObject(item);
                }
            }
        }
    }

    [Cmdlet(VerbsCommon.Get, "Process", DefaultParameterSetName = "Name")]
    [OutputType(typeof(System.Diagnostics.Process))]
    public sealed class GetProcessCommand : PSCmdlet
    {
        [Parameter(Position = 0, ParameterSetName = "Name", ValueFromPipelineByPropertyName = true)]
        [Alias("ProcessName")]
        [ValidateNotNullOrEmpty]
        public string[] Name { get; set; }

        [Parameter(ParameterSetName = "Id", Mandatory = true, ValueFromPipelineByPropertyName = true)]
        [Alias("PID")]
        public int[] Id { get; set; }

        protected override void ProcessRecord()
        {
            Laptop.Answer(this, "Get-LaptopProcess", "Microsoft.PowerShell.Commands.GetProcessCommand");
        }
    }

    [Cmdlet(VerbsCommon.Get, "PSDrive", DefaultParameterSetName = "Name")]
    [OutputType(typeof(PSDriveInfo))]
    public sealed class GetPSDriveCommand : PSCmdlet
    {
        [Parameter(Position = 0, ParameterSetName = "Name", ValueFromPipelineByPropertyName = true)]
        [ValidateNotNullOrEmpty]
        public string[] Name { get; set; }

        [Parameter(Position = 1, ValueFromPipelineByPropertyName = true)]
        public string[] PSProvider { get; set; }

        protected override void ProcessRecord()
        {
            Laptop.Answer(this, "Get-LaptopDrive", "Microsoft.PowerShell.Commands.GetPSDriveCommand");
        }
    }

    // PowerShell has Get-ComputerInfo on Windows only: without the facts there is none.
    [Cmdlet(VerbsCommon.Get, "ComputerInfo")]
    public sealed class GetComputerInfoCommand : PSCmdlet
    {
        [Parameter(Position = 0)]
        public string[] Property { get; set; }

        protected override void ProcessRecord()
        {
            Laptop.Answer(this, "Get-LaptopComputerInfo", null);
        }
    }
}
