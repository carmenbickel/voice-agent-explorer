# RAG baseline evaluation report

Generator: baseline-eval-v1

- **questions**: 12
- **mean_recall**: 1.0
- **abstention_respected**: True

| Topic | Question | Expected | Retrieved | Recall |
| --- | --- | --- | --- | --- |
| policy:returns | How long do I have to return unworn shoes? | article-returns-policy | article-returns-policy, article-returns-policy | 1.0 |
| policy:exchange | Can I exchange size 39 for a size 40 of the same product? | article-exchanges-policy | article-exchanges-policy | 1.0 |
| policy:cancellation | Can I cancel a shipped order? | article-cancellation-policy | article-cancellation-policy, article-shipping-overview | 1.0 |
| policy:shipping | When do demo orders arrive? | article-shipping-overview | article-returns-policy, article-shipping-overview | 1.0 |
| sizing | Which EU sizes do Stepwise Shoes use? | article-sizing-guide | article-sizing-guide, article-fit-and-comfort | 1.0 |
| fit | How much space should toes have in new shoes? | article-fit-and-comfort | article-fit-and-comfort | 1.0 |
| care | How should I clean inflatable waterproof hiking shoes? | article-care-dfg-products | article-care-dfg-products, article-fit-and-comfort | 1.0 |
| technology | Is the demo membrane breathable? | article-waterproof-technology | article-waterproof-technology | 1.0 |
| running | What kind of shoes fit short city runs? | article-running-form | article-running-form, article-fit-and-comfort, article-sizing-guide | 1.0 |
| demo-limits | Are payments real in this demo? | article-faq-demo | article-faq-demo, article-shipping-overview | 1.0 |
| handover | What happens if my problem is more complex than a return? | article-support-handover | article-support-handover | 1.0 |
| abstention | Turquoise tie-dye wedding shoes with 3-inch heels | — | — | 1.0 |
