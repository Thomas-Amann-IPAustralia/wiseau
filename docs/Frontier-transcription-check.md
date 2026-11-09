V5N1 p18 · Two comparison tables printed side by side, a variety pedigree list below them
Blind: Yes

1st. sha256 495a28b77531ba66… verified
2nd. sha256 e31c20313c31eab6… verified
3rd. sha256 ad4f0ed4cb060a27… verified

Notes: 
- The third seemed odd when I opened it in it's rendered view. The table didn't exist.
- Additionally, all failed to bold or assign a heading level to "TRITICALE", "Description" and "Origin".
- For a perfect representation I would have expected to see text in bold and italics where appropriate ("'Abacus'", "25 November 1991", "Luminis Pty. Ltd.,"Puccinia graminis ssp. tritici")
- The "ad4f0ed4cb060a27" has differences because it uses straight quotes instead of fancy ones (I think)

---

V4 p19 · A five-variety comparison table above two columns of prose
Blind: Yes

1st. sha256 b59325d842df1215… verified
2nd. sha256 165b852f29a65d0c… verified
3rd. sha256 564b74c2c3306eaf… verified

Notes: 
- 2nd did better than 3rd because, even though it's table didn't render it was still easier to parse in an unrendered view

---

V2 p12 · A wide four-variety comparison table with wrapped cells
Blind: Yes

1st. sha256 7bbc1469a369fb61… verified
2nd. sha256 2ef56bba09a2134d… verified
3rd. sha256 0f462e1de66c4356… verified

Notes: 
- It was tight between 1st and 2nd. I preferred the one that came 1st because it rendered well but for an unrendered representation the 2nd one did extremely well.
- 3rd was surprisingly rough. All of the text seemed to be correct but there is one contender here that seems to really struggle with structure.

---

V4N1 p7 · Two columns of descriptions with a data table, scanned at 200 dpi
Blind: Yes

1st. sha256 e6802acd6bed8df4… verified
2nd. sha256 a5033e31685b0567… verified
3rd. sha256 1b10494552eb9c68… verified

Notes:
- I'm now at the point where I'm essentially operating on the assumption that all text is being equally captured and so it is just a matter of accurately representing the document's structure (as this is a clear point of difference between the 3.
- sha256 e6802acd6bed8df4… split "COROLLA" and "LENGTH" across two rows despite it being one heading. As it did with "FLOWER WING WIDTH". I know the document shows them on two lines but if we're splitting hairs, this is erroneous.
- My expectation is that if we're working with markdown files we should be using markdown syntax to improve legibility. Otherwise, it may as well be a .txt file. If, for instance, I would later like to chunk the OCR'd documents, I will have a much easier time if I can have headings, subheadings, tables and other information which can be represented in markdown to be done with markdown.
- With the above said, IF I chose to represent the documents in pure text, 2nd has done it very well.

---

P63a p7 · A 1963 typewritten patent specification with handwritten margin marks
Blind: Yes

1st. Three-way tie
2nd. N/A
3rd. N/A

Notes:
- All models were able to very accurately capture the text on the page. The number at the bottom (the only point of difference that I could spot) is unclear even to me and I think "- 7" or "10 - 7" are both valid interpretations.

---

Conclusion:

The final text was not very easy to parse for me, a human, so the fact that each one performed so well further demonstrates the notion that text recognition is, for my purposes, a 'solved' problem. What remains is accurately reconstructing the document structure and its syntax.

Hopefully the JSON interim representation which the wiseau pipeline has recently implemented may help on this front.

