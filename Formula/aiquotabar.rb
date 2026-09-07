class Aiquotabar < Formula
  desc "macOS menu bar app showing live Claude.ai and ChatGPT usage limits"
  homepage "https://github.com/yagcioglutoprak/AIQuotaBar"
  url "https://github.com/yagcioglutoprak/AIQuotaBar/archive/refs/tags/v1.1.0.tar.gz"
  sha256 "9c875f01e4891e4483640abcf1447e172dcf66ddfc49f46df91187ea19c4f5ff"
  license "MIT"
  head "https://github.com/yagcioglutoprak/AIQuotaBar.git", branch: "main"

  depends_on macos: :monterey
  depends_on "python@3.12"

  def install
    venv = libexec/"venv"
    system "python3.12", "-m", "venv", venv

    # pyobjc-core tries to read $HOME during build; point it at a writable dir
    ENV["HOME"] = buildpath

    # Install from requirements.txt rather than pinned sdist resources.
    # curl-cffi's sdist runs a build script that writes to a hardcoded
    # /Users/runner path, so the resource-based install died with
    # PermissionError before ever reaching the app. The wheels install
    # cleanly, and this matches what install.sh already does.
    system venv/"bin/pip", "install", "--upgrade", "pip"
    system venv/"bin/pip", "install", "-r", "requirements.txt"

    # claude_bar.py is only a shim - it does `from aiquotabar.__main__ import
    # main` - so the package must ship alongside it, or the launcher exits
    # with ModuleNotFoundError.
    libexec.install "claude_bar.py"
    libexec.install "aiquotabar"
    (libexec/"assets").install Dir["assets/*"]

    # Fix rumps notification crash (requires CFBundleIdentifier in Info.plist)
    plist_path = venv/"bin/Info.plist"
    unless plist_path.exist?
      system "/usr/libexec/PlistBuddy", "-c",
             "Add :CFBundleIdentifier string rumps", plist_path.to_s
    end

    (bin/"aiquotabar").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/python" "#{libexec}/claude_bar.py" "$@"
    SH
    chmod 0755, bin/"aiquotabar"
  end

  def caveats
    <<~EOS
      AIQuotaBar is a macOS menu bar app. Launch it with:
        aiquotabar &

      To run it at login, click the ◆ icon in your menu bar → Launch at Login.

      Logs are written to: ~/.claude_bar.log
    EOS
  end

  test do
    # Import the package rather than only byte-compiling the shim: py_compile
    # passes even when the aiquotabar package is missing entirely, which is
    # how the broken install went unnoticed.
    ENV["PYTHONPATH"] = libexec
    system "#{libexec}/venv/bin/python", "-c", "import aiquotabar, aiquotabar.__main__"
  end
end
