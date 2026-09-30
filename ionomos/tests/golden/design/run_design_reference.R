# limma / DEqMS reference for designs with blocks and covariates, the moderated F and DEqMS.
# Usage (from this folder): Rscript run_design_reference.R [R library with limma and DEqMS]
# Checked with limma 3.68.5 and DEqMS 1.30.0. Factor levels are in order of appearance, as Ionomos builds them.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages({ library(limma); library(DEqMS) })

sd <- read.delim("design_samples.tsv", stringsAsFactors = FALSE)
full <- as.matrix(read.delim("design_matrix.tsv", row.names = 1, check.names = FALSE))
miss <- as.matrix(read.delim("design_matrix_missing.tsv", row.names = 1, check.names = FALSE))
counts <- read.delim("design_counts.tsv", row.names = 1)
lev <- function(v) factor(v, levels = unique(v))
condition <- lev(sd$condition)
replicate <- lev(as.character(sd$replicate))
sex <- lev(sd$sex)
age <- sd$age
w <- function(df, f) write.table(format(df, digits = 15), f, sep = "\t", quote = FALSE, row.names = FALSE)
scal <- NULL
note <- function(name, fit) {
  scal <<- rbind(scal, data.frame(model = name, df.prior = format(fit$df.prior, digits = 15),
                                  s2.prior = format(fit$s2.prior, digits = 15)))
}

tables <- function(name, y, design, cntrst, write = TRUE) {
  colnames(design) <- make.names(colnames(design))
  fit <- lmFit(y, design)
  made <- makeContrasts(contrasts = cntrst, levels = design)
  eb <- eBayes(contrasts.fit(fit, made))
  note(name, eb)
  out <- NULL
  for (k in seq_along(cntrst)) {
    tt <- topTable(eb, coef = k, number = Inf, sort.by = "none", confint = TRUE)
    out <- rbind(out, data.frame(comparison = cntrst[k], ID = rownames(tt), logFC = tt$logFC, CI.L = tt$CI.L,
                                 CI.R = tt$CI.R, t = tt$t, P.Value = tt$P.Value, adj.P.Val = tt$adj.P.Val))
  }
  if (write) w(out, paste0("design_", name, ".tsv"))
  invisible(eb)
}
f_table <- function(name, eb) {  # topTableF on a fit whose contrasts are every condition vs DMSO
  tf <- suppressMessages(topTableF(eb, number = Inf, sort.by = "none"))
  w(data.frame(ID = rownames(tf), F = tf$F, P.Value = tf$P.Value, adj.P.Val = tf$adj.P.Val),
    paste0("design_F_", name, ".tsv"))
}
vs <- c("conditionDrugA - conditionDMSO", "conditionDrugB - conditionDMSO")
pairs <- c(vs, "conditionDrugB - conditionDrugA")

# 1. block = replicate (paired / batch), complete data: every pair, then the F on the two contrasts vs DMSO
X <- model.matrix(~0 + condition + replicate)
tables("block", full, X, pairs)
f_table("block", tables("block_vs", full, X, vs, FALSE))
# 2. the same with missing values per row (non-orthogonal design: contrasts.fit's correlation approximation)
tables("block_missing", miss, X, pairs)
f_table("block_missing", tables("block_missing_vs", miss, X, vs, FALSE))
# 3. covariates: a numeric (age) and a factor (sex), with the block
Xc <- model.matrix(~0 + condition + replicate + age + sex)
f_table("covariates", tables("covariates", full, Xc, vs))
# 4. the plain model's F (~0 + condition), for experiments with no block
X0 <- model.matrix(~0 + condition)
f_table("plain", tables("plain_vs", full, X0, vs, FALSE))
f_table("plain_missing", tables("plain_missing_vs", miss, X0, vs, FALSE))
# 5. one condition against the rest, with the block (test_limma type = "others" plus the replicate)
out <- NULL
for (c in levels(condition)) {
  is_c <- as.numeric(condition == c)
  Xo <- cbind(IS = is_c, NOT = 1 - is_c, model.matrix(~replicate)[, -1])
  fit <- lmFit(full, Xo)
  eb <- eBayes(contrasts.fit(fit, makeContrasts(contrasts = "IS - NOT", levels = Xo)))
  note(paste0("others_", c), eb)
  tt <- topTable(eb, coef = 1, number = Inf, sort.by = "none", confint = TRUE)
  out <- rbind(out, data.frame(comparison = paste0(c, "_vs_others"), ID = rownames(tt), logFC = tt$logFC,
                               CI.L = tt$CI.L, CI.R = tt$CI.R, t = tt$t, P.Value = tt$P.Value,
                               adj.P.Val = tt$adj.P.Val))
}
w(out, "design_block_others.tsv")

# 6. DEqMS spectraCounteBayes on the blocked fit (complete data, and with missing values)
deqms <- function(name, y) {
  fit <- lmFit(y, X)

  eb <- eBayes(contrasts.fit(fit, makeContrasts(contrasts = vs, levels = X)))
  eb$count <- counts[rownames(eb$coefficients), "count"]
  dq <- spectraCounteBayes(eb)
  scal <<- rbind(scal, data.frame(model = paste0("deqms_", name), df.prior = format(dq$sca.dfprior, digits = 15),
                                  s2.prior = NA))
  out <- NULL
  for (k in seq_along(vs)) {
    out <- rbind(out, data.frame(comparison = vs[k], ID = rownames(dq$coefficients), logFC = dq$coefficients[, k],
                                 sca.t = dq$sca.t[, k], sca.P.Value = dq$sca.p[, k],
                                 sca.adj.pval = p.adjust(dq$sca.p[, k], method = "BH"),
                                 sca.postvar = dq$sca.postvar, sca.priorvar = dq$sca.priorvar,
                                 loess = fitted(dq$model)))
  }
  w(out, paste0("design_deqms_", name, ".tsv"))
}
deqms("block", full)
deqms("block_missing", miss)
# 7. DEqMS on the plain model
fit <- lmFit(full, X0)
eb <- eBayes(contrasts.fit(fit, makeContrasts(contrasts = vs, levels = X0)))
eb$count <- counts[rownames(eb$coefficients), "count"]
dq <- spectraCounteBayes(eb)
scal <- rbind(scal, data.frame(model = "deqms_plain", df.prior = format(dq$sca.dfprior, digits = 15), s2.prior = NA))
w(data.frame(ID = rownames(dq$coefficients), sca.t = dq$sca.t[, 1], sca.P.Value = dq$sca.p[, 1],
             sca.postvar = dq$sca.postvar), "design_deqms_plain.tsv")
w(scal, "design_priors.tsv")
cat("limma", as.character(packageVersion("limma")), "DEqMS", as.character(packageVersion("DEqMS")), "\n")
