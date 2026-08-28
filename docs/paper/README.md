# OpenMycelium research and invention package

This directory contains the publication-oriented description of the Mycelium
placement algorithm and MCCL runtime contract.

## Deliverables

- `OPENMYCELIUM_MYCELIUM_MCCL_IEEE_MANUSCRIPT.md`: editable source
- `artifacts/OpenMycelium_Mycelium_MCCL_IEEE_Manuscript.docx`: polished Word manuscript
- `artifacts/OpenMycelium_Mycelium_MCCL_IEEE_Manuscript.pdf`: publication PDF

The `artifacts/legacy/` directory preserves the pre-rename HetCCL-branded
artifacts as historical records. They are not current OpenMycelium outputs and
must not be relabeled as MCCL builds.
- `assets/openmycelium_architecture.png`: architecture figure
- `assets/mycelium_decision_flow.png`: evidence-gated algorithm figure
- `build_paper.py`: reproducible document and diagram builder

## Publication warning

The manuscript is an engineering preprint, not a patentability opinion or an
official IEEE submission template. Complete the author and affiliation fields,
run a professional prior-art search, and have patent counsel review Appendix A
before further public disclosure if patent protection is desired.

## Rebuild

Use the Python runtime with `python-docx` and Pillow available:

```powershell
python docs/paper/build_paper.py
```

The generated DOCX must be rendered and visually inspected before release.
