 
# DLL Decompiler – Cross-Platform Edition

A Python-based .NET DLL extractor and decompiler that converts managed .NET DLL assemblies into readable C# source code using ILSpy.

Supports Windows, Linux, Kali Linux, and macOS.

## Features

- **Cross-platform:** Windows, Linux, Kali Linux, and macOS.
- **Single DLL extraction:** Decompile an individual `.dll` file.
- **Bulk DLL extraction:** Automatically discover and process DLL files inside directories and subdirectories.
- **Automatic dependency installation:** Downloads and configures .NET SDK/runtime and ILSpy when required.
- **Framework filtering:** Option to skip .NET framework DLLs.
- **Offline mode:** Run using previously installed dependencies.
- **SHA-256 identification:** Generates a SHA-256 hash for each input DLL.
- **Decompilation cache:** Reuses previously decompiled results.
- **Multithreaded processing:** Processes multiple DLLs concurrently.
- **Progress tracking:** Displays processing progress and status.
- **Logging:** Saves execution logs for each decompilation session.
- **Windows long-path support:** Handles extended Windows file paths.
- **Portable tool directory:** Keeps downloaded dependencies inside the project directory when using the bundled installation.

## Technology Stack

| Component | Technology |
|---|---|
| Programming language | Python |
| Decompilation engine | ILSpy |
| Decompiler CLI | ilspycmd |
| Framework | .NET 10 |
| Hashing | SHA-256 |
| Processing | Python ThreadPoolExecutor |
| Supported platforms | Windows, Linux, macOS |

## Requirements

- Python 3.8 or newer.
- Internet connection for initial dependency installation.
- PowerShell on Windows for automatic .NET installation.
- Bash and curl or wget on Linux/macOS for automatic .NET installation.
- Sufficient disk space for .NET SDK/runtime and decompiled output.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR-USERNAME/DLL-Decompiler.git
```

### 2. Navigate to the project

```bash
cd DLL-Decompiler
```

### 3. Run the extractor

No additional Python packages are required by the supplied script.

## Usage

### Extract a single DLL

```bash
python decompile_only.py application.dll
```

### Extract all DLL files from a directory

```bash
python decompile_only.py ./DLLs
```

### Skip .NET framework DLLs

```bash
python decompile_only.py ./DLLs --skip-framework
```

### Run in offline mode

```bash
python decompile_only.py application.dll --offline
```

### Combine options

```bash
python decompile_only.py ./DLLs --offline --skip-framework
```

## Output Structure

The tool creates a timestamped output directory for every execution.

```text
DLL-Decompiler/
│
├── decompile_only.py
├── README.md
├── LICENSE
├── .gitignore
│
├── tools/
│   ├── dotnet/
│   └── ilspy/
│
├── .dllsec_cache/
│   └── decompiled/
│
└── Decompiled/
    └── YYYYMMDD_HHMMSS/
        ├── decompile.log
        ├── Application_XXXXXXXX/
        │   ├── *.cs
        │   └── ...
        └── Library_XXXXXXXX/
            ├── *.cs
            └── ...
```

The eight-character suffix represents the beginning of the DLL's SHA-256 hash.

## Processing Workflow

1. Accepts a DLL file or directory.
2. Discovers DLL files recursively.
3. Calculates the SHA-256 hash of each DLL.
4. Checks whether previously decompiled source code is available in the cache.
5. Verifies or installs the .NET SDK/runtime and ILSpy.
6. Executes ILSpy to decompile managed .NET assemblies.
7. Exports readable C# source files.
8. Stores results in a timestamped output directory.
9. Generates execution logs and processing statistics.

## Command-Line Options

| Option | Description |
|---|---|
| `<file.dll>` | Decompile a single DLL |
| `<directory>` | Recursively discover DLL files |
| `--offline` | Disable automatic dependency downloads |
| `--skip-framework` | Skip identified .NET framework DLLs |

## Limitations

- Primarily designed for managed .NET assemblies.
- Native/unmanaged DLLs are not supported as ordinary C# decompilation inputs.
- Decompiled source code may differ from the original source code.
- Original comments, formatting, and some source-level information may not be recoverable.
- Obfuscated assemblies may produce difficult-to-understand output.
- Offline operation requires the necessary dependencies to be installed and verified beforehand.

## Legal and Responsible Use

This tool is intended for authorized software analysis, debugging, interoperability research, security assessments, and educational purposes.

Only analyze DLL files that you own or are legally authorized to inspect. Respect software licenses, intellectual property rights, and applicable laws.

## Credits

- [ILSpy](https://github.com/icsharpcode/ILSpy) – .NET assembly browser and decompiler.
- [.NET](https://dotnet.microsoft.com/) – Application framework and runtime.

## Author

**YOUR-NAME**

GitHub: https://github.com/Swanoop10082001

---

**Disclaimer:** This project is provided for educational and authorized software analysis purposes. The author is not responsible for misuse of this software.
