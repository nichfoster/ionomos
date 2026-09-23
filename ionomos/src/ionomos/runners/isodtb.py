"""
isoDTB post-processing: merge modified peptides to labelled sites.

Port of reference/lab-scripts/isoDTB_Fragpipe_merge-individual-peptides-to-Site.R

Contract (Phase 2):
    merge_to_sites(input_tsv, output_tsv, sample_prefix, mod_mass="561.3387") -> pd.DataFrame

    input_tsv     = <workdir>/combined_modified_peptide_label_quant.tsv
    sample_prefix = FragPipe experiment name (RawName.sample)
    Algorithm (mirror the R exactly, then golden-file test against a real
    lab output):
      - for each row, find every "[<mod_mass>]" in `Light Modified Peptide`
      - residue letter = char before '['; position in peptide = count of
        letters before '['; position in protein = Start + pos - 1
      - join Protein, Protein ID, Entry Name, Gene, Protein Description,
        Peptide Sequence, and all "<sample_prefix>_<n> Log2 Ratio HL" columns
      - group by (Protein, Protein ID, Entry Name, Gene, Protein Description,
        ModifiedResidue, ResiduePositionInProtein)
      - PeptideCount = n distinct peptides; ExamplePeptides = first 3 joined "; "
      - Mean_<col> per ratio column (nan-mean); Mean_Log2_Ratio_HL over all
      - sort by Protein, ResiduePositionInProtein; write TSV
    Errors: no labelled sites found; no ratio columns match the prefix.
"""
from __future__ import annotations


def merge_to_sites(input_tsv, output_tsv, sample_prefix, mod_mass="561.3387"):
    raise NotImplementedError("Phase 2")
