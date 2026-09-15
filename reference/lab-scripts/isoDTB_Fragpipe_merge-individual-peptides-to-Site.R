#!/usr/bin/env Rscript

# --- Load libraries ---
suppressPackageStartupMessages({
  library(tidyverse)
  library(stringr)
})

# --- USER SETTINGS ---
input_tsv  <-"C:/Fragpipe_General/EJQ/isoDTB_EJQ-2-027_10uM_2h/combined_modified_peptide_label_quant.tsv"   		# adjust path
output_tsv <- "C:/Fragpipe_General/EJQ/isoDTB_EJQ-2-027_10uM_2h/combined_modified_peptide_label_quant_output.tsv" #adjust path
sample_prefix <- "isoDTB_EJQ_2_027_10uM_2h" #-set to your sample prefix

# --- Read FragPipe results ---
df <- read_tsv(input_tsv, show_col_types = FALSE)

# --- Helper: extract labeled sites (robust position calculation) ---
extract_labeled_sites <- function(mod_col, start_col, row_ids, mod_mass = "561.3387") {
  # pattern matching the modification mass in brackets, e.g. [561.3387]
  pattern <- paste0("\\[", mod_mass, "\\]")
  modified_positions <- str_locate_all(mod_col, pattern)

  res_list <- lapply(seq_along(modified_positions), function(i) {
    mods <- modified_positions[[i]]
    if (nrow(mods) == 0) return(NULL)

    # For each modification occurrence in this modified-peptide string
    tibble_row <- map_dfr(seq_len(nrow(mods)), function(j) {
      mod_start_char <- mods[j, 1]  # char index where '[' begins
      # take prefix up to the char before '['
      prefix <- str_sub(mod_col[i], 1, mod_start_char - 1)
      # Count letters (A-Z, case-insensitive) in the prefix -> gives residue index in peptide
      pos_in_peptide <- str_count(prefix, "[A-Za-z]")
      # residue letter immediately before bracket (if exists)
      residue_letter <- str_sub(prefix, -1, -1)

      tibble(
        Row = row_ids[i],
        ModifiedPeptide = mod_col[i],
        ModifiedResidue = residue_letter,
        ResiduePositionInPeptide = pos_in_peptide
      )
    })
    tibble_row
  })

  results <- bind_rows(res_list)
  if (nrow(results) > 0) {
    # attach Start for each Row and compute protein position
    results <- results %>%
      mutate(Start = start_col[Row]) %>%
      mutate(ResiduePositionInProtein = Start + ResiduePositionInPeptide - 1)
  }
  results
}

# --- Extract labeled sites from LIGHT column only ---
# We will keep the original row id for safe joining back
row_ids <- seq_len(nrow(df))
light_mod_col <- df$`Light Modified Peptide`
start_col <- df$Start

light_sites <- extract_labeled_sites(
  mod_col = light_mod_col,
  start_col = start_col,
  row_ids = row_ids,
  mod_mass = "561.3387"
)

if (is.null(light_sites) || nrow(light_sites) == 0) {
  stop("No labeled sites found in Light Modified Peptide column with mass 561.3387.")
}

# --- Merge site rows back to the original data using Row id ---
# Select protein metadata and ratio columns, keep Row to join
meta_and_ratios <- df %>%
  mutate(Row = row_number()) %>%
  select(
    Row,
    Protein,
    `Protein ID` = any_of("Protein ID"),
    `Entry Name` = any_of("Entry Name"),
    Gene = any_of("Gene"),
    `Protein Description` = any_of("Protein Description"),
    Peptide = `Peptide Sequence`,
    contains("Log2 Ratio HL")
  )

merged <- light_sites %>%
  left_join(meta_and_ratios, by = "Row")

# --- Identify ratio replicate columns for the given sample prefix ---
ratio_cols <- grep(
  paste0("^", sample_prefix, "_[0-9]+ Log2 Ratio HL$"),
  colnames(merged),
  value = TRUE
)

if (length(ratio_cols) == 0) {
  stop("No matching Log2 Ratio HL columns found for sample prefix. Check sample_prefix value.")
}

# --- Summarize by protein site (Protein + ResiduePositionInProtein) ---
summary_df <- merged %>%
  group_by(
    Protein,
    `Protein ID`,
    `Entry Name`,
    Gene,
    `Protein Description`,
    ModifiedResidue,
    ResiduePositionInProtein
  ) %>%
  summarise(
    PeptideCount = n_distinct(Peptide),
    ExamplePeptides = paste(unique(Peptide)[1:min(3, length(unique(Peptide)))], collapse = "; "),
    across(all_of(ratio_cols), ~mean(.x, na.rm = TRUE), .names = "Mean_{col}"),
    Mean_Log2_Ratio_HL = mean(c_across(all_of(ratio_cols)), na.rm = TRUE),
    .groups = "drop"
  ) %>%
  arrange(Protein, ResiduePositionInProtein)

# --- Save output ---
write_tsv(summary_df, output_tsv)
cat("\n✅ Labeled-site summary written to:", output_tsv, "\n")
print(head(summary_df, 10))
