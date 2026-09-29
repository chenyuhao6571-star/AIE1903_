# AIE-1903 · CV Reader

Python + React demo: upload PDF/DOCX, extract text, generate a structured candidate profile with DeepSeek, and browse five profile sections.

```powershell
cd code
.\setup.ps1
.\start.ps1
```

Open http://127.0.0.1:8000. See [setup, configuration and tests](code/README.md).

API keys, course materials, candidate documents and generated profiles are not included. Configure `DEEPSEEK_API_KEY` or `DEEPSEEK_KEY_FILE` on your machine. The optional sample button uses a local file at `data/simple_dataset/CV.pdf`.
