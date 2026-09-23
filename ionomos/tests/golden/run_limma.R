# limma reference for the moderated t-test. Usage: Rscript run_limma.R [R library with limma]
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages(library(limma))
x <- read.delim("limma_two_group.tsv", row.names = 1)
grp <- factor(c(rep("A", 4), rep("B", 3)), levels = c("B", "A"))
fit <- eBayes(lmFit(as.matrix(x), model.matrix(~grp)))
tt <- topTable(fit, coef = 2, number = Inf, sort.by = "none")
write.table(data.frame(id = rownames(tt), logFC = tt$logFC, t = tt$t, p = tt$P.Value, q = tt$adj.P.Val),
            "limma_two_group_out.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
cat("two-group df.prior", fit$df.prior, "s2.prior", fit$s2.prior, "\n")
y <- read.delim("limma_one_sample.tsv", row.names = 1)
fit1 <- eBayes(lmFit(as.matrix(y), matrix(1, ncol(y), 1)))
tt1 <- topTable(fit1, coef = 1, number = Inf, sort.by = "none")
write.table(data.frame(id = rownames(tt1), logFC = tt1$logFC, t = tt1$t, p = tt1$P.Value, q = tt1$adj.P.Val),
            "limma_one_sample_out.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
cat("one-sample df.prior", fit1$df.prior, "s2.prior", fit1$s2.prior, "\n")
