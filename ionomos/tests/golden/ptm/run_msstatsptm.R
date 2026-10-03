# MSstatsPTM reference for the protein-abundance correction of isoDTB site ratios (D70, downstream/proteincorr.py).
# Usage (from this folder): Rscript run_msstatsptm.R [R library with limma and MSstatsPTM]
# Checked with limma 3.68.5 and MSstatsPTM 2.14.0 (R 4.6.1).
#   sites    ptm_sites.tsv: log2 heavy / light per replicate. Ionomos' moderated one-sample test (min_valid = 2):
#            a site with fewer than 2 values is not tested, so its values are set to NA before lmFit; then
#            lmFit(~1), eBayes; SE = sqrt(s2.post) * stdev.unscaled, DF = df.total.
#   protein  ptm_protein.tsv: the proteome's Cmpd vs DMSO as MSstats groupComparison writes it. On the sites' scale
#            (liganded_direction: high, the compound-treated sample carries the light tag) the protein's log2 H/L
#            is -log2FC.
#   adjust   MSstatsPTM:::.applyPtmAdjustment (MSstatsPTM's own .adjustProteinLevel + BH per Label): log2FC =
#            site - protein, SE = sqrt(SE_site^2 + SE_protein^2), Satterthwaite DF, two-sided p. Sites whose
#            protein is missing are dropped by MSstatsPTM (Ionomos keeps and flags them).
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(strsplit(args[1], ":")[[1]], .libPaths()))
suppressPackageStartupMessages({library(limma); library(MSstatsPTM); library(data.table)})

y <- as.matrix(read.delim("ptm_sites.tsv", row.names = 1, check.names = FALSE))
y[rowSums(!is.na(y)) < 2, ] <- NA
fit <- eBayes(lmFit(y, matrix(1, ncol(y), 1)))
se <- sqrt(fit$s2.post) * fit$stdev.unscaled[, 1]
prot_of <- sapply(strsplit(rownames(y), "|", fixed = TRUE), `[`, 2)
site <- data.table(Protein = prot_of, Site = rownames(y), Label = "Cmpd-DMSO", log2FC = fit$coefficients[, 1],
                   SE = se, DF = fit$df.total)
site <- site[!is.na(SE)]
protein <- fread("ptm_protein.tsv")
protein[, DF := as.numeric(DF)]
protein[, log2FC := -log2FC]   # compound vs DMSO -> heavy / light (the treated sample is light)
adj <- MSstatsPTM:::.applyPtmAdjustment("Cmpd-DMSO", site, protein)
out <- merge(adj, site[, .(Site, site_log2FC = log2FC, site_SE = SE, site_DF = DF)], by = "Site")
setorder(out, Site)
fwrite(out[, .(Site, Protein, log2FC, SE, DF, Tvalue, pvalue, adj.pvalue, site_log2FC, site_SE, site_DF)],
       "ptm_adjusted.tsv", sep = "\t", na = "NA", quote = FALSE)
cat("sites tested", nrow(site), "adjusted", nrow(adj), "df.prior", fit$df.prior, "s2.prior", fit$s2.prior,
    "MSstatsPTM", as.character(packageVersion("MSstatsPTM")), "limma", as.character(packageVersion("limma")), "\n")
