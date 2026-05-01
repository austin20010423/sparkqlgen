# Part 2: The Multi-Model Eval Challenge

**Objective:** Design an evaluation pipeline to test how well different models perform at generating the correct structured queries for your domain.

## Requirements
- [ ] **1. Data Generation**
  - Programmatically gather or generate **30** realistic, diverse natural language queries
  - Include edge cases, messy phrasing, and complex constraints

- [ ] **2. Ground Truth**
  - Write the perfect, correct structured query for each input
  - Test cases must be complex, adversarial, and nuanced enough to actively break the models

- [ ] **3. Execution & Iteration**
  - Run evaluation pipeline using **at least 3 different models**
  - Must be a mix of **open-weight AND closed-source** models
    for open weights i'll like to use Llama4 model in ollama and i'll like to run on macos m4 macbook air, please change gpt-4-mini to Llama4 in ollama that can run on macbook air m4 16g 

- [ ] **4. The Threshold**
  - Iterate on prompts and pipeline until **ALL** chosen models hit **>85% accuracy** against the manually labeled set

- [ ] **5. Write-up (in README)**
  - [ ] **Model Selection:** How did you choose these specific 3+ models? Why were they capable of hitting the accuracy threshold across the board?
  - [ ] **Performance:** Compare how the models performed. What patterns did they initially get wrong?
  - [ ] **Learnings:** What did you learn about eval design and building ground truth for structured outputs?
