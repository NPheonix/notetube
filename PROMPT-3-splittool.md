Some of my PDFs are 500 MB or more, which is too big for Streamlit Cloud.
Please make a simple tool I can run on my own computer: split_pdf.py.
- It takes a big PDF and splits it into parts using the same smart chapter-wise splitting as the app.
- Each part must be under 150 MB so it uploads easily to the app.
- It saves the parts in a folder next to the PDF with clear names like Part1_Ch1-4.pdf.
- I should be able to run it by just typing: .\venv\Scripts\python.exe split_pdf.py "path\to\book.pdf"
- It must work on a 500 MB file without using too much memory.
- Add the steps to the README in simple words. Test it on a big generated PDF without using any Gemini requests.
Ask me before pushing to GitHub.
