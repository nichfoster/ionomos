# FragPipeAnalystR reference (v1.1.1 logic, base R + limma instead of SummarizedExperiment/dplyr).
# Usage: Rscript run_fpa_reference.R [R library with limma]
# Transcribed from FragPipeAnalystR R/imputation.R manual_impute() and R/DE.R test_limma();
# the web app (FragPipe-Analyst) uses the same code (manual_impute_customized, test_limma_customized).
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages(library(limma))

raw <- as.matrix(read.delim("fpa_matrix.tsv", row.names = 1, check.names = FALSE))
design_df <- read.delim("fpa_design.tsv", stringsAsFactors = FALSE)

# ---- impute(fun = "man"): drop all-NA rows, then manual_impute(scale = 0.3, shift = 1.8, seed = 123)
num_NAs <- rowSums(is.na(raw))
x <- raw[num_NAs != ncol(raw), ]
samples <- sort(colnames(x), method = "radix")          # dplyr::group_by() order (C locale)
set.seed(123)
for (s in samples) {
  v <- x[, s]; v <- v[!is.na(v)]
  infin <- nrow(x) - length(v)
  x[is.na(x[, s]), s] <- rnorm(infin, mean = median(v) - 1.8 * sd(v), sd = sd(v) * 0.3)
}
write.table(data.frame(ID = rownames(x), x, check.names = FALSE), "fpa_imputed.tsv",
            sep = "\t", quote = FALSE, row.names = FALSE)

# ---- test_limma(se, type, control)
test_limma <- function(raw, col_data, type, control = NULL) {
  condition <- factor(col_data$condition)
  design <- model.matrix(~ 0 + condition)
  colnames(design) <- gsub("condition", "", colnames(design))
  conditions <- as.character(unique(col_data$condition))
  if (type == "all") {
    cntrst <- apply(utils::combn(conditions, 2), 2, paste, collapse = " - ")
    if (!is.null(control)) {
      flip <- grep(paste("^", control, sep = ""), cntrst)
      if (length(flip) >= 1) {
        cntrst[flip] <- paste(gsub(paste(control, "- ", sep = " "), "", cntrst[flip]), " - ", control, sep = "")
      }
    }
  } else if (type == "control") {
    cntrst <- paste(conditions[!conditions %in% control], control, sep = " - ")
  } else if (type == "others") {
    for (i in seq_along(conditions)) {
      design <- cbind(design, ifelse(design[, conditions[i]], 0, 1))
      colnames(design)[ncol(design)] <- paste0("NOT_", conditions[i])
    }
    out <- NULL
    for (c in conditions) {
      sub_design <- design[, c(c, paste0("NOT_", c))]
      fit <- lmFit(raw, design = sub_design)
      made <- makeContrasts(contrasts = paste0(c, "-", "NOT_", c), levels = sub_design)
      eB <- eBayes(contrasts.fit(fit, made))
      tt <- topTable(eB, sort.by = "none", adjust.method = "BH", coef = 1, number = Inf, confint = TRUE)
      out <- rbind(out, data.frame(comparison = paste0(c, "_vs_others"), ID = rownames(tt), diff = tt$logFC,
                                   CI.L = tt$CI.L, CI.R = tt$CI.R, t = tt$t, p.val = tt$P.Value,
                                   p.adj = tt$adj.P.Val, check.names = FALSE))
    }
    return(out)
  }
  fit <- lmFit(raw, design = design)
  made <- makeContrasts(contrasts = cntrst, levels = design)
  contrast_fit <- contrasts.fit(fit, made)
  if (any(is.na(raw))) {
    for (i in cntrst) {
      covariates <- unlist(strsplit(i, " - "))
      single_contrast <- makeContrasts(contrasts = i, levels = design[, covariates])
      single_fit <- contrasts.fit(fit[, covariates], single_contrast)
      contrast_fit$coefficients[, i] <- single_fit$coefficients[, 1]
      contrast_fit$stdev.unscaled[, i] <- single_fit$stdev.unscaled[, 1]
    }
  }
  eB <- eBayes(contrast_fit)
  cat(type, "df.prior", format(eB$df.prior, digits = 15), "s2.prior", format(eB$s2.prior, digits = 15), "\n")
  out <- NULL
  for (comp in cntrst) {
    tt <- topTable(eB, sort.by = "none", adjust.method = "BH", coef = comp, number = Inf, confint = TRUE)
    out <- rbind(out, data.frame(comparison = gsub(" - ", "_vs_", comp), ID = rownames(tt), diff = tt$logFC,
                                 CI.L = tt$CI.L, CI.R = tt$CI.R, t = tt$t, p.val = tt$P.Value,
                                 p.adj = tt$adj.P.Val, check.names = FALSE))
  }
  out
}
cd <- design_df[match(colnames(x), design_df$sample_name), ]
w <- function(df, f) write.table(format(df, digits = 15), f, sep = "\t", quote = FALSE, row.names = FALSE)
w(test_limma(x, cd, "all", control = "DMSO"), "fpa_limma_all_imputed.tsv")
w(test_limma(x, cd, "control", control = "DMSO"), "fpa_limma_control_imputed.tsv")
w(test_limma(x, cd, "others"), "fpa_limma_others_imputed.tsv")
# no imputation: limma on the matrix with missing values (the per-contrast refit path)
y <- raw[num_NAs != ncol(raw), ]
w(test_limma(y, cd, "control", control = "DMSO"), "fpa_limma_control_missing.tsv")
