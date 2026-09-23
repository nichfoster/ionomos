# Runs the lab's R scripts (reference/lab-scripts) unmodified except for their USER INPUT lines,
# on the golden inputs. Usage: Rscript run_r_scripts.R [R library with dplyr/stringr/readr/tidyr/purrr/tibble]
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
ref <- "../../../reference/lab-scripts"
run <- function(script, subs) {
  code <- readLines(file.path(ref, script), warn = FALSE)
  code <- sub("library\\(tidyverse\\)", "library(dplyr); library(tidyr); library(purrr); library(readr); library(tibble)", code)
  for (k in names(subs)) code <- sub(paste0("^\\s*", k, "\\s*<-.*$"), paste0(k, " <- \"", subs[[k]], "\""), code)
  eval(parse(text = code), envir = new.env())
}
for (p in c("isoDTB_EJQ_2_027", "isoDTB_EJQ_2_028")) {
  run("isoDTB_Fragpipe_merge-individual-peptides-to-Site.R",
      list(input_tsv = "isodtb_label_quant.tsv", output_tsv = paste0("R_", p, "_sites.tsv"), sample_prefix = p))
}
run("correct_experimental_annotation_for_Fragpipe-TMT.R",
    list(input_tsv = "tmt_abundance_gene_MD.tsv", output_tsv = "R_experimental_annotation.tsv"))
