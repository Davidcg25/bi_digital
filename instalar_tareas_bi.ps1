<#
Instala o actualiza las tareas programadas de BI local.

Uso recomendado despues de formatear:
  1. Abre PowerShell como tu usuario normal.
  2. Ejecuta:
     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
     & "D:\Proyectos\4_BI_Ecom\instalar_tareas_bi.ps1"

Edita Triggers en $Tasks si necesitas mover la ventana de ejecucion.
#>

$ErrorActionPreference = "Stop"

$BaseDir = "D:\Proyectos\4_BI_Ecom"
$TaskFolder = "\Digital Impact BI\"
$StartupDir = [Environment]::GetFolderPath("Startup")
$Ga4StartupBatch = Join-Path $BaseDir "medios_di_ga4_solidez_startup_delayed.bat"
$Ga4StartupShortcut = Join-Path $StartupDir "medios_di_ga4_solidez_delayed.lnk"
$SalesWeekendStartupBatch = Join-Path $BaseDir "ventas_solidez_startup_weekend_delayed.bat"
$SalesWeekendStartupShortcut = Join-Path $StartupDir "ventas_solidez_weekend_delayed.lnk"
$Weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

$Tasks = @(
    @{
        Name = "sincronizacion_di_solidez"
        Batch = Join-Path $BaseDir "sincronizacion_di_solidez.bat"
        Triggers = @(
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "09:00" },
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "12:00" },
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "15:00" },
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "18:00" }
        )
        Description = "Ventas Solidez RMH -> SQL -> Google Sheets"
    },
    @{
        Name = "sincronizacion_di_solidez_stock"
        Batch = Join-Path $BaseDir "sincronizacion_di_solidez-stock.bat"
        Triggers = @(
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "14:30" }
        )
        Description = "Stock Solidez RMH -> SQL -> Google Sheets"
    },
    @{
        Name = "complaint_books_solidez"
        Batch = Join-Path $BaseDir "complaint_books_solidez.bat"
        Triggers = @(
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "11:30" }
        )
        Description = "Libro de reclamos -> SQL -> Google Sheets"
    },
    @{
        Name = "seo_performance_web_solidez"
        Batch = Join-Path $BaseDir "seo_performance_web_solidez.bat"
        Triggers = @(
            @{ Type = "Weekly"; DaysOfWeek = @("Monday"); Time = "10:00" },
            @{ Type = "Weekly"; DaysOfWeek = @("Thursday"); Time = "17:30" }
        )
        Description = "PageSpeed SEO Performance Web -> SQL Server"
    },
    @{
        Name = "clarity_solidez"
        Batch = Join-Path $BaseDir "Clarity\clarity_solidez.bat"
        Triggers = @(
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "10:45" }
        )
        Description = "Microsoft Clarity live insights -> SQL Server"
    },
    @{
        Name = "medios_di_ga4_solidez"
        Batch = Join-Path $BaseDir "medios_di_ga4_solidez.bat"
        Triggers = @(
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "10:15" }
        )
        Description = "GA4 Solidez -> SQL Server -> Google Sheets"
    },
    @{
        Name = "diagnostico_digest"
        Batch = Join-Path $BaseDir "Diagnostico\digest.bat"
        Triggers = @(
            @{ Type = "Weekly"; DaysOfWeek = $Weekdays; Time = "18:30" }
        )
        Description = "Digest accionable de diagnostico -> HTML local + briefs agencia (post refresh diario)"
    }
)

function New-BiTriggers {
    param(
        [Parameter(Mandatory)] [array] $TriggerSpecs
    )

    $Triggers = @()
    foreach ($Spec in $TriggerSpecs) {
        switch ($Spec.Type.ToLowerInvariant()) {
            "daily" { $Triggers += New-ScheduledTaskTrigger -Daily -At $Spec.Time }
            "weekly" {
                $DaysOfWeek = if ($Spec.DaysOfWeek) { $Spec.DaysOfWeek } else { @("Monday") }
                $Triggers += New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DaysOfWeek -At $Spec.Time
            }
            "atlogon" {
                $Trigger = New-ScheduledTaskTrigger -AtLogOn
                if ($Spec.Delay) {
                    $Delay = [TimeSpan]::Parse($Spec.Delay)
                    $Trigger.Delay = "PT$([int]$Delay.TotalMinutes)M"
                }
                $Triggers += $Trigger
            }
            default { throw "Trigger no soportado: $($Spec.Type). Usa Daily, Weekly o AtLogOn." }
        }
    }
    return $Triggers
}

foreach ($Task in $Tasks) {
    if (-not (Test-Path -LiteralPath $Task.Batch)) {
        Write-Warning "No existe el batch para $($Task.Name): $($Task.Batch). Se omite."
        continue
    }

    $Action = New-ScheduledTaskAction `
        -Execute "cmd.exe" `
        -Argument "/c `"$($Task.Batch)`"" `
        -WorkingDirectory $BaseDir

    $Trigger = New-BiTriggers -TriggerSpecs $Task.Triggers

    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
        -RestartCount 2 `
        -RestartInterval (New-TimeSpan -Minutes 10)

    $Principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME `
        -LogonType Interactive `
        -RunLevel Limited

    $ScheduledTask = New-ScheduledTask `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description $Task.Description

    Register-ScheduledTask `
        -TaskPath $TaskFolder `
        -TaskName $Task.Name `
        -InputObject $ScheduledTask `
        -Force | Out-Null

    $TriggerSummary = ($Task.Triggers | ForEach-Object {
        if ($_.Type -eq "Daily") {
            "Daily $($_.Time)"
        } elseif ($_.Type -eq "Weekly") {
            "Weekly $($_.DaysOfWeek -join ',') $($_.Time)"
        } elseif ($_.Type -eq "AtLogOn" -and $_.Delay) {
            "AtLogOn + $($_.Delay)"
        } else {
            $_.Type
        }
    }) -join ", "
    Write-Host "OK: $TaskFolder$($Task.Name) -> $TriggerSummary"
}

function Set-StartupShortcut {
    param(
        [Parameter(Mandatory)] [string] $ShortcutPath,
        [Parameter(Mandatory)] [string] $TargetPath,
        [Parameter(Mandatory)] [string] $Description
    )

    if (-not (Test-Path -LiteralPath $TargetPath)) {
        throw "No existe el batch de inicio: $TargetPath"
    }

    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetPath
    $Shortcut.WorkingDirectory = $BaseDir
    $Shortcut.Description = $Description
    $Shortcut.Save()
}

if (Test-Path -LiteralPath $Ga4StartupShortcut) {
    Remove-Item -LiteralPath $Ga4StartupShortcut -Force
    Write-Host "OK: acceso directo GA4 de inicio removido; GA4 corre por tarea programada"
}

Set-StartupShortcut `
    -ShortcutPath $SalesWeekendStartupShortcut `
    -TargetPath $SalesWeekendStartupBatch `
    -Description "Ejecuta ventas Solidez sabado/domingo 15 minutos despues de iniciar sesion"
Write-Host "OK: inicio de Windows -> ventas sab/dom + 15 minutos ($SalesWeekendStartupShortcut)"

Write-Host "Listo. Revisa las tareas con: Get-ScheduledTask -TaskPath '$TaskFolder'"
