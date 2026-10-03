# limma reference for a competition experiment with unequal groups (D66): DMSO n=2, Probe n=4, Probe_Comp n=4.
# Usage (from this folder): Rscript run_unequal_reference.R [R library with limma]
# Checked with limma 3.68.5. What Ionomos does with these settings, in base R + limma:
#   filter     FragPipe-Analyst filter_by_condition: a value in >= 50 % of the samples of at least one condition
#   normalise  median centring (FragPipeAnalystR MD_normalization, moved to the median of the sample medians)
#   impute     none, or Perseus-type (FragPipeAnalystR manual_impute(scale = 0.3, shift = 1.8), set.seed(123))
#   model      ~0 + condition; the role comparisons (roles.py): Probe - DMSO, Probe_Comp - Probe, Probe_Comp - DMSO;
#              FragPipeAnalystR test_limma's per-contrast refit when values are missing; one eBayes for all
#   min_valid  without imputation a feature is tested in a comparison only with min_valid = 2 values in each group;
#              with small_group_min_valid = "half" the smaller group of an unequal comparison needs half its
#              samples (DMSO: 1 of 2). The coefficient of an untested feature is set to NA before eBayes, so the
#              variance prior is fitted on every feature, as Ionomos does, and BH counts only the tested ones.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages(library(limma))

raw <- as.matrix(read.delim("unequal_matrix.tsv", row.names = 1, check.names = FALSE))
sd_ <- read.delim("unequal_samples.tsv", stringsAsFactors = FALSE)
cond <- sd_$condition[match(colnames(raw), sd_$sample)]
w <- function(df, f) write.table(format(df, digits = 15), f, sep = "\t", quote = FALSE, row.names = FALSE)

# ---- filter_by_condition(50 %)
present <- !is.na(raw)
keep <- apply(present, 1, function(p) any(tapply(p, cond, mean) >= 0.5 - 1e-12))
x <- raw[keep, , drop = FALSE]
# ---- median normalisation
meds <- apply(x, 2, median, na.rm = TRUE)
x <- sweep(x, 2, meds) + median(meds)
x <- x[rowSums(!is.na(x)) > 0, , drop = FALSE]
w(data.frame(ID = rownames(x), x, check.names = FALSE), "unequal_processed.tsv")

# ---- Perseus-type imputation
imputed <- x
set.seed(123)
for (s in sort(colnames(imputed), method = "radix")) {
  v <- imputed[, s]; v <- v[!is.na(v)]
  infin <- nrow(imputed) - length(v)
  imputed[is.na(imputed[, s]), s] <- rnorm(infin, mean = median(v) - 1.8 * sd(v), sd = sd(v) * 0.3)
}
w(data.frame(ID = rownames(imputed), imputed, check.names = FALSE), "unequal_imputed.tsv")

contrasts <- c("Probe - DMSO", "Probe_Comp - Probe", "Probe_Comp - DMSO")
needs <- function(a, b, rule, min_valid = 2) {
  na <- sum(cond == a); nb <- sum(cond == b)
  need <- c(min_valid, min_valid)
  if (rule == "half" && na != nb) {
    k <- if (na < nb) 1 else 2
    need[k] <- min(min_valid, max(1, ceiling(min(na, nb) / 2)))
  }
  need
}
priors <- NULL
test_limma <- function(name, y, rule = NULL) {
  condition <- factor(cond)
  design <- model.matrix(~ 0 + condition)
  colnames(design) <- gsub("condition", "", colnames(design))
  fit <- lmFit(y, design = design)
  made <- makeContrasts(contrasts = contrasts, levels = design)
  contrast_fit <- contrasts.fit(fit, made)
  if (any(is.na(y))) {
    for (i in contrasts) {
      covariates <- unlist(strsplit(i, " - "))
      single <- contrasts.fit(fit[, covariates], makeContrasts(contrasts = i, levels = design[, covariates]))
      contrast_fit$coefficients[, i] <- single$coefficients[, 1]
      contrast_fit$stdev.unscaled[, i] <- single$stdev.unscaled[, 1]
    }
  }
  if (!is.null(rule)) {
    for (i in contrasts) {
      ab <- unlist(strsplit(i, " - "))
      need <- needs(ab[1], ab[2], rule)
      na <- rowSums(!is.na(y[, cond == ab[1], drop = FALSE]))
      nb <- rowSums(!is.na(y[, cond == ab[2], drop = FALSE]))
      contrast_fit$coefficients[!(na >= need[1] & nb >= need[2]), i] <- NA
    }
  }
  eB <- eBayes(contrast_fit)
  priors <<- rbind(priors, data.frame(case = name, df.prior = format(eB$df.prior, digits = 15),
                                      s2.prior = format(eB$s2.prior, digits = 15)))
  out <- NULL
  for (comp in contrasts) {
    tt <- topTable(eB, sort.by = "none", adjust.method = "BH", coef = comp, number = Inf, confint = TRUE)
    out <- rbind(out, data.frame(comparison = gsub(" - ", " vs ", comp), ID = rownames(tt), diff = tt$logFC,
                                 CI.L = tt$CI.L, CI.R = tt$CI.R, t = tt$t, p.val = tt$P.Value,
                                 p.adj = tt$adj.P.Val, check.names = FALSE))
  }
  w(out, paste0("unequal_limma_", name, ".tsv"))
}
test_limma("half", x, "half")
test_limma("same", x, "same")
test_limma("imputed", imputed)
w(priors, "unequal_priors.tsv")
cat("limma", as.character(packageVersion("limma")), "\n")
