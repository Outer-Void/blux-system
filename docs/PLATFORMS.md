# Platforms

This repository is in Phase 0 (hygiene only). Use the setup below to prepare a
basic Python environment for future development.

## Termux (Android, native)

```sh
pkg install python3
```

## proot Debian (Termux + proot)

```sh
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
```

## Desktop Linux (Debian/Ubuntu)

```sh
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
```

## macOS (Homebrew)

```sh
brew install python3
```

## Windows (PowerShell + winget)

```powershell
winget install --id Python.Python.3
```
