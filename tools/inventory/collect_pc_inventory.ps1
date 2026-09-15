# Proteomics PC inventory collector
# ------------------------------------------------------------------
# Paste this whole block into an elevated PowerShell 5.1 window on the
# proteomics PC. Writes a report folder + zip to the Desktop.
#
# Changes vs. the original (reference/pc-inventory/collect_pc_inventory.original.rtf):
#   * Read-Host paths are stripped of surrounding quotes (fixes the
#     false "DOES NOT EXIST YET" results from the 2026-09-15 run)
#   * C:\Fragpipe_General, C:\FragPipe and C:\Proteomics_File_Sharing
#     are added to the search roots
#   * name filter widened to catch .raw/.workflow/.fp-manifest/annotation
#     files and TMT/DIA folders, not just isoDTB
# ------------------------------------------------------------------
& {
    $ErrorActionPreference = "SilentlyContinue"
    $ProgressPreference = "SilentlyContinue"

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $desktopPath = [Environment]::GetFolderPath("Desktop")
    $outputDir = Join-Path $desktopPath "Proteomics_PC_Inventory_$stamp"
    $reportPath = Join-Path $outputDir "system_report.txt"
    $zipPath = "$outputDir.zip"

    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

    function Add-Section {
        param([string]$Title)

        Add-Content -Path $reportPath -Value "`r`n============================================================"
        Add-Content -Path $reportPath -Value $Title
        Add-Content -Path $reportPath -Value "============================================================"
    }

    function Add-Output {
        param(
            [string]$Title,
            [scriptblock]$Command
        )

        Add-Section $Title

        try {
            $result = & $Command 2>&1 | Out-String -Width 300
            Add-Content -Path $reportPath -Value $result
        }
        catch {
            Add-Content -Path $reportPath -Value "ERROR: $($_.Exception.Message)"
        }
    }

    function Get-ExistingParent {
        param([string]$Path)

        $candidate = $Path

        while ($candidate -and -not (Test-Path -LiteralPath $candidate)) {
            $candidate = Split-Path -Path $candidate -Parent
        }

        return $candidate
    }

    function Document-Path {
        param(
            [string]$Label,
            [string]$Path
        )

        Add-Section "PATH: $Label"
        Add-Content -Path $reportPath -Value "Entered path: $Path"

        if ([string]::IsNullOrWhiteSpace($Path)) {
            Add-Content -Path $reportPath -Value "No path provided."
            return
        }

        if (Test-Path -LiteralPath $Path) {
            Add-Content -Path $reportPath -Value "Status: EXISTS"

            $item = Get-Item -LiteralPath $Path

            Add-Content -Path $reportPath -Value (
                $item |
                Select-Object FullName, PSIsContainer, CreationTime, LastWriteTime, Attributes |
                Format-List |
                Out-String -Width 300
            )

            Add-Content -Path $reportPath -Value "`r`nPermissions:"

            try {
                $acl = Get-Acl -LiteralPath $Path

                Add-Content -Path $reportPath -Value (
                    $acl |
                    Select-Object Path, Owner, AccessToString |
                    Format-List |
                    Out-String -Width 300
                )
            }
            catch {
                Add-Content -Path $reportPath -Value "Could not read permissions."
            }

            if ($item.PSIsContainer) {
                Add-Content -Path $reportPath -Value "`r`nImmediate contents, maximum 250 items:"

                Add-Content -Path $reportPath -Value (
                    Get-ChildItem -LiteralPath $Path -Force |
                    Select-Object -First 250 Name, FullName, PSIsContainer, Length, LastWriteTime |
                    Format-Table -AutoSize |
                    Out-String -Width 300
                )
            }
        }
        else {
            Add-Content -Path $reportPath -Value "Status: DOES NOT EXIST YET"

            $existingParent = Get-ExistingParent -Path $Path
            Add-Content -Path $reportPath -Value "Nearest existing parent: $existingParent"

            if ($existingParent) {
                try {
                    $acl = Get-Acl -LiteralPath $existingParent

                    Add-Content -Path $reportPath -Value (
                        $acl |
                        Select-Object Path, Owner, AccessToString |
                        Format-List |
                        Out-String -Width 300
                    )
                }
                catch {
                    Add-Content -Path $reportPath -Value "Could not read parent permissions."
                }
            }
        }
    }

    Clear-Host
    Write-Host ""
    Write-Host "Proteomics PC inventory collector" -ForegroundColor Cyan
    Write-Host "Press Enter to leave an unknown or inapplicable path blank."
    Write-Host ""

    # NOTE: paths are un-quoted (Trim '"') — the 2026-09-15 run passed literal quote
    # characters to Test-Path, so every path check reported DOES NOT EXIST YET.
    $sharedPath = (Read-Host "Ethernet/shared-folder path receiving Eclipse data").Trim('"').Trim()
    $watchPath = (Read-Host "Planned watcher/drop-folder path").Trim('"').Trim()
    $exampleInput = Read-Host "Representative experiment folder path(s), separated by semicolons"
    $fragPipePath = (Read-Host "FragPipe installation folder, if known").Trim('"').Trim()
    $downstreamInput = Read-Host "isoDTB or other downstream script folder path(s), separated by semicolons"

    $examplePaths = @(
        $exampleInput -split ";" |
        ForEach-Object { $_.Trim().Trim('"') } |
        Where-Object { $_ }
    )

    $downstreamPaths = @(
        $downstreamInput -split ";" |
        ForEach-Object { $_.Trim().Trim('"') } |
        Where-Object { $_ }
    )

    Set-Content -Path $reportPath -Value @"
PROTEOMICS PC INVENTORY
Collected: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Computer: $env:COMPUTERNAME
User running inventory: $env:USERDOMAIN\$env:USERNAME

This report records system configuration, relevant paths, filenames,
permissions, software versions, and storage information.
It does not copy raw files or read experiment-file contents.
"@

    Add-Output "WINDOWS AND POWERSHELL" {
        Get-ComputerInfo |
        Select-Object WindowsProductName,
                      WindowsVersion,
                      OsName,
                      OsVersion,
                      OsArchitecture,
                      CsDomain,
                      CsPartOfDomain,
                      CsSystemType

        "`r`nPowerShell:"
        $PSVersionTable
    }

    Add-Output "CPU" {
        Get-CimInstance Win32_Processor |
        Select-Object Name,
                      Manufacturer,
                      NumberOfCores,
                      NumberOfLogicalProcessors,
                      MaxClockSpeed
    }

    Add-Output "MEMORY" {
        Get-CimInstance Win32_ComputerSystem |
        Select-Object @{
            Name = "TotalRAM_GB"
            Expression = { [math]::Round($_.TotalPhysicalMemory / 1GB, 2) }
        }

        Get-CimInstance Win32_OperatingSystem |
        Select-Object @{
            Name = "FreeRAM_GB"
            Expression = { [math]::Round($_.FreePhysicalMemory * 1KB / 1GB, 2) }
        }
    }

    Add-Output "DISKS AND NETWORK DRIVES" {
        Get-CimInstance Win32_LogicalDisk |
        Select-Object DeviceID,
                      VolumeName,
                      FileSystem,
                      DriveType,
                      @{
                          Name = "Size_GB"
                          Expression = {
                              if ($_.Size) {
                                  [math]::Round($_.Size / 1GB, 2)
                              }
                          }
                      },
                      @{
                          Name = "Free_GB"
                          Expression = {
                              if ($_.FreeSpace) {
                                  [math]::Round($_.FreeSpace / 1GB, 2)
                              }
                          }
                      },
                      ProviderName |
        Format-Table -AutoSize
    }

    Add-Output "PHYSICAL DISKS" {
        Get-PhysicalDisk |
        Select-Object FriendlyName,
                      MediaType,
                      BusType,
                      HealthStatus,
                      OperationalStatus,
                      @{
                          Name = "Size_GB"
                          Expression = { [math]::Round($_.Size / 1GB, 2) }
                      } |
        Format-Table -AutoSize
    }

    Add-Output "SMB AND MAPPED NETWORK CONNECTIONS" {
        "SMB mappings:"
        Get-SmbMapping |
        Select-Object LocalPath, RemotePath, Status

        "`r`nPowerShell filesystem drives:"
        Get-PSDrive -PSProvider FileSystem |
        Select-Object Name, Root, DisplayRoot, Description
    }

    Add-Output "RELEVANT COMMAND LOCATIONS" {
        foreach ($commandName in @(
            "java",
            "python",
            "python3",
            "py",
            "R",
            "Rscript",
            "dotnet",
            "git"
        )) {
            $command = Get-Command $commandName -ErrorAction SilentlyContinue

            if ($command) {
                [PSCustomObject]@{
                    Command = $commandName
                    Path = $command.Source
                    Version = $command.Version
                }
            }
            else {
                [PSCustomObject]@{
                    Command = $commandName
                    Path = "NOT FOUND IN PATH"
                    Version = ""
                }
            }
        }
    }

    Add-Output "SOFTWARE VERSION OUTPUT" {
        "JAVA:"
        & java -version 2>&1

        "`r`nPYTHON:"
        & python --version 2>&1

        "`r`nPY LAUNCHER:"
        & py --version 2>&1

        "`r`nR:"
        & R --version 2>&1 |
        Select-Object -First 5

        "`r`nGIT:"
        & git --version 2>&1
    }

    Add-Output "RELEVANT INSTALLED PROGRAMS" {
        $registryLocations = @(
            "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
        )

        Get-ItemProperty $registryLocations |
        Where-Object {
            $_.DisplayName -match "FragPipe|MSFragger|Philosopher|IonQuant|Python|Java|R Project|Thermo|Proteome|DIA|TMT"
        } |
        Select-Object DisplayName, DisplayVersion, Publisher, InstallLocation |
        Sort-Object DisplayName -Unique |
        Format-Table -AutoSize
    }

    Document-Path -Label "Eclipse shared folder" -Path $sharedPath
    Document-Path -Label "Planned watcher folder" -Path $watchPath
    Document-Path -Label "FragPipe installation" -Path $fragPipePath

    $downstreamNumber = 0

    foreach ($downstreamPath in $downstreamPaths) {
        $downstreamNumber++
        Document-Path -Label "Downstream pipeline $downstreamNumber" -Path $downstreamPath
    }

    Add-Section "AUTOMATIC FRAGPIPE AND PIPELINE SEARCH"
    Add-Content -Path $reportPath -Value "Limited-depth search of common locations; this may take a few minutes."

    $searchRoots = @(
        $fragPipePath,
        $desktopPath,
        (Join-Path ([Environment]::GetFolderPath("UserProfile")) "Downloads"),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        $env:PUBLIC,
        "C:\Fragpipe_General",
        "C:\FragPipe",
        "C:\Proteomics_File_Sharing",
        "D:\",
        "E:\"
    ) |
    Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
    Select-Object -Unique

    $softwareMatches = foreach ($searchRoot in $searchRoots) {
        Get-ChildItem -LiteralPath $searchRoot -Depth 5 -Force |
        Where-Object {
            $_.Name -match "FragPipe|MSFragger|IonQuant|Philosopher|isoDTB|DIA-NN|Spectronaut|TMT|DIA|\.raw$|\.workflow$|\.fp-manifest$|annotation"
        } |
        Select-Object FullName,
                      Name,
                      PSIsContainer,
                      Length,
                      LastWriteTime
    }

    $softwareMatches |
    Sort-Object FullName -Unique |
    Export-Csv (Join-Path $outputDir "software_path_candidates.csv") -NoTypeInformation

    Add-Content -Path $reportPath -Value (
        $softwareMatches |
        Sort-Object FullName -Unique |
        Format-Table -AutoSize |
        Out-String -Width 300
    )

    $exampleNumber = 0

    foreach ($examplePath in $examplePaths) {
        $exampleNumber++
        Document-Path -Label "Representative experiment $exampleNumber" -Path $examplePath

        if (Test-Path -LiteralPath $examplePath -PathType Container) {
            $basePath = (Get-Item -LiteralPath $examplePath).FullName.TrimEnd("\")

            $manifest = Get-ChildItem -LiteralPath $basePath -Recurse -Force |
            Select-Object @{
                              Name = "RelativePath"
                              Expression = {
                                  $_.FullName.Substring($basePath.Length).TrimStart("\")
                              }
                          },
                          FullName,
                          PSIsContainer,
                          Extension,
                          Length,
                          CreationTime,
                          LastWriteTime,
                          Attributes

            $manifestPath = Join-Path $outputDir "experiment_${exampleNumber}_manifest.csv"
            $manifest | Export-Csv $manifestPath -NoTypeInformation

            $extensionSummaryPath = Join-Path $outputDir "experiment_${exampleNumber}_file_types.csv"

            $manifest |
            Where-Object { -not $_.PSIsContainer } |
            Group-Object Extension |
            ForEach-Object {
                [PSCustomObject]@{
                    Extension = if ($_.Name) { $_.Name } else { "[no extension]" }
                    FileCount = $_.Count
                    TotalSize_GB = [math]::Round(
                        (($_.Group | Measure-Object Length -Sum).Sum / 1GB),
                        4
                    )
                }
            } |
            Sort-Object FileCount -Descending |
            Export-Csv $extensionSummaryPath -NoTypeInformation
        }
    }

    Add-Output "RUNNING RELEVANT PROCESSES" {
        Get-Process |
        Where-Object {
            $_.ProcessName -match "FragPipe|java|python|Rscript|Philosopher|IonQuant|MSFragger"
        } |
        Select-Object ProcessName,
                      Id,
                      Path,
                      CPU,
                      StartTime,
                      @{
                          Name = "RAM_GB"
                          Expression = { [math]::Round($_.WorkingSet64 / 1GB, 3) }
                      } |
        Format-Table -AutoSize
    }

    Add-Output "RELEVANT WINDOWS SERVICES" {
        Get-CimInstance Win32_Service |
        Where-Object {
            $_.Name -match "Frag|Proteom|Python|Java" -or
            $_.DisplayName -match "Frag|Proteom|Python|Java"
        } |
        Select-Object Name, DisplayName, State, StartMode, StartName, PathName |
        Format-List
    }

    Add-Output "NETWORK PATH CONNECTIVITY" {
        foreach ($pathToTest in @($sharedPath, $watchPath, $fragPipePath) + $downstreamPaths) {
            if ($pathToTest) {
                [PSCustomObject]@{
                    Path = $pathToTest
                    Exists = Test-Path -LiteralPath $pathToTest
                }
            }
        }
    }

    Add-Section "COLLECTION SUMMARY"
    Add-Content -Path $reportPath -Value @"
Shared folder entered: $sharedPath
Watcher folder entered: $watchPath
FragPipe folder entered: $fragPipePath
Representative experiment folders: $($examplePaths -join "; ")
Downstream folders: $($downstreamPaths -join "; ")

Generated files:
- system_report.txt
- software_path_candidates.csv
- one experiment manifest per representative folder
- one file-type summary per representative folder
"@

    Compress-Archive -Path (Join-Path $outputDir "*") -DestinationPath $zipPath -Force

    Write-Host ""
    Write-Host "Inventory complete." -ForegroundColor Green
    Write-Host "Upload this ZIP to Dropbox:" -ForegroundColor Cyan
    Write-Host $zipPath -ForegroundColor Yellow
    Write-Host ""
    Write-Host "The uncompressed report folder was also left on the Desktop."
}