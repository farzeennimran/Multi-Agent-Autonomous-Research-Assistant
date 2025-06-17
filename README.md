# Multi-Agent-Autonomous-Research-Assistant

- Dataset was taken from: arXiv summarization dataset https://huggingface.co/datasets/ccdv/arxiv-summarization (abstracts + full papers) 
- Pre-trained LLM model i.e. Mistral 7B with LoRA fine-tuning
- Training Goal was to summarize research papers accurately using minimal trainable parameters
- Evaluation was done using ROUGE, BLEU, BERTScore, and LLM-as-a-Judge for qualitative evaluation

The system consists of five specialized agents orchestrated using LangGraph:

### 1. KeywordAgent
Enhances the user’s input by generating expanded and related keywords using an LLM, improving the accuracy and coverage of search queries.

### 2. SearchAgent
Interfaces with academic search APIs (e.g., arXiv, Semantic Scholar, PubMed) to retrieve relevant papers based on the expanded keywords.

### 3. RankAgent
Scores and ranks papers using a multi-criteria strategy including:

- Citation count
- Publication date
- Relevance to keywords (inferred through an external LLM API such as Together.ai)

### 4. SummaryAgent
Processes selected top-ranked papers and generates structured summaries using a LoRA fine-tuned language model which you already trained for academic summarization.
