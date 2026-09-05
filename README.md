# Windowscript

> A domain-specific language (DSL) for GUI programming, transpiled to Python + tkinter

![Version](https://img.shields.io/badge/version-2.1.1-00ffcc?style=flat-square)
![License](https://img.shields.io/badge/license-Closed%20Source-ff4466?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows-0078d4?style=flat-square)

---

## ✨ Features

- **Easy to learn** – Minimal keywords, syntax similar to JavaScript/Java
- **Object-oriented** – Classes, encapsulation (`private`), constructors
- **Native GUI** – Declarative window, label, button, and image components
- **Python interop** – Import any Python library and embed raw Python code
- **Code etiquette** – `#translate` / `#appreciated` / `#sorry` – polite programming enforced

---

## 📥 Download

### Latest Stable Release: v2.1.1

[⬇️ Download Windowscript IDE (Windows .exe)](https://github.com/your-username/your-repo/releases/latest)

> Requirements: Windows 7 or later. No Python installation needed.

---

## 📖 Documentation

- [Language Manual (PDF)](./docs/Windowscript语法书-从入门到入土.pdf)
- [Online Examples](http://escd.top/wdoc)

---

## 🛠️ Quick Example

```windowscript
make.window { "Hello" } => {
    word("Welcome to Windowscript", 20 [200, 80], "#00ffcc")
    button("Click Me", 16 [200, 180], "#66ffdd") {
        pr "Button clicked!"
    }
}
