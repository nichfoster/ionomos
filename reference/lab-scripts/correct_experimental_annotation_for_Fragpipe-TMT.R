# Load required libraries
library(dplyr)
library(stringr)
library(readr)

# ---- USER INPUT ----
input_tsv <- "C:/Fragpipe_General/Aman/22Rv1_AM2012_PD_lys_output/tmt-report/abundance_gene_MD.tsv"      			# path to your input file
output_tsv <- "C:/Fragpipe_General/Aman/22Rv1_AM2012_PD_lys_output/tmt-report/experimental_annotation-corr.tsv"   	# output metadata file
defined_sample_name <- "22Rv1_AM2012_PD"     						# your chosen sample name

# ---- READ HEADER ----
headers <- read_tsv(input_tsv, n_max = 0) %>% colnames()

# ---- FILTER HEADERS THAT MATCH PATTERN ----
valid_pattern <- "^[A-Za-z0-9]+_1_\\d{3}[A-Z]?$"
filtered_headers <- headers[str_detect(headers, valid_pattern)]

# ---- BUILD METADATA TABLE ----
metadata <- tibble(original_header = filtered_headers) %>%
  mutate(
    # plex: extracted from the "_1_" part
    plex = str_extract(original_header, "(?<=_)(\\d+)(?=_)"),
    
    # channel: 126, 127N, 128C, etc.
    channel = str_extract(original_header, "(\\d{3}[A-Z]?)$"),
    
    # sample: full header as-is
    sample = original_header,
    
    # sample_name: same as sample but without "_1_"
    sample_name = str_replace(original_header, "_1_", "_"),
    
    # condition: the prefix before first underscore (XXX)
    condition = str_extract(original_header, "^[^_]+")
  ) %>%
  # add replicate count within each condition
  group_by(condition) %>%
  mutate(replicate = row_number()) %>%
  ungroup() %>%
  select(plex, channel, sample, sample_name, condition, replicate)

# ---- WRITE OUTPUT ----
write_tsv(metadata, output_tsv)

