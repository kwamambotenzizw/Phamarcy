$ErrorActionPreference = 'Stop'
$env:PHARMA_SMTP_USER = 'nyashamusingafi23@gmail.com'
$env:PHARMA_SMTP_FROM_NAME = 'Thachi Investiments'

$pythonExe = Join-Path $env:LocalAppData 'Python/pythoncore-3.14-64/python.exe'
if (-not (Test-Path $pythonExe)) {
    $pythonExe = (Get-Command py -ErrorAction Stop).Source
    $pythonArgs = @('-3', (Join-Path $PSScriptRoot '..\server.py'))
} else {
    $pythonArgs = @((Join-Path $PSScriptRoot '..\server.py'))
}

if (-not $env:PHARMA_SMTP_PASSWORD) {
    $securePassword = Read-Host 'Enter the Gmail App Password for nyashamusingafi23@gmail.com (email sending; leave blank to skip)' -AsSecureString
    if ($securePassword.Length -gt 0) {
        $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
        try {
            $env:PHARMA_SMTP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
        }
    }
}

try {
    & $pythonExe @pythonArgs
} finally {
    Remove-Item Env:PHARMA_SMTP_PASSWORD -ErrorAction SilentlyContinue
}
