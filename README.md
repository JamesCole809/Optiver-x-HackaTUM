# Optiver-x-HackaTUM
Write up for the Optiver x HackaTUM challenge 2025

## Introduction

This project was built for the HackaTUM 2025 Optibook challenge. The setting is a simulated electronic exchange with five stocks, each dual-listed on two venues, traded simultaneously by human teams and background bots. In addition to the limit order books, a live news channel injects stock-specific and market-wide headlines that can move prices. Our goal was to design an automated trader that starts as a robust market maker, then progressively layers in news-awareness, cross-listing arbitrage, inventory control, and finally sentiment-driven directional trading. The core implementation lives in `MidMMPrimaryBot` in `main.py`. :contentReference[oaicite:0]{index=0}  

We began with a mid-price market making engine. For each instrument we compute a VWAP mid from both sides of the book,
\[
\text{VWAP}_\text{bid} = 
\frac{\sum_i p_i^{(b)} q_i^{(b)}}{\sum_i q_i^{(b)}},
\qquad
\text{VWAP}_\text{ask} = 
\frac{\sum_j p_j^{(a)} q_j^{(a)}}{\sum_j q_j^{(a)}},
\]
\[
m_t = \frac{\text{VWAP}_\text{bid} + \text{VWAP}_\text{ask}}{2},
\]
and quote symmetrically around this fair value with a half-spread \(s_t\),
\[
b_t = m_t - s_t, \qquad a_t = m_t + s_t.
\]
Whenever we buy at \(b_t\) and subsequently sell at \(a_t\), the pure spread capture per share is
\[
\text{PnL}_\text{spread} = a_t - b_t = 2s_t,
\]
so our first objective was simply to maximise the number of such round trips while staying passive and within position limits.

The news feed is then used to adapt our risk and pricing. Stock-specific headlines widen the half-spread for the affected name, while global macro headlines widen spreads across all instruments for a short window. Conceptually, our effective half-spread is
\[
s_t^\text{eff} = \max\bigl(s_\text{base},\; s_\text{stock\_news}(t),\; s_\text{global\_news}(t),\; \tfrac12(a_t^\text{book}-b_t^\text{book})\bigr),
\]
making us more conservative exactly when uncertainty is high, but still allowing us to tighten up when the book itself is tight.

With five dual-listed stocks, the next step was to exploit cross-venue arbitrage. For stock \(i\) we track mid prices on venue A and B, \(m_t^{(i,A)}\) and \(m_t^{(i,B)}\), and define the instantaneous mispricing
\[
\Delta_t^{(i)} = m_t^{(i,A)} - m_t^{(i,B)}.
\]
When \(|\Delta_t^{(i)}| > \theta\) for a threshold \(\theta\) covering fees and noise, we lock in the discrepancy by buying the cheap venue and selling the expensive one:
\[
\text{if } \Delta_t^{(i)} > \theta: \quad \text{sell on A, buy on B}, \qquad
\text{if } \Delta_t^{(i)} < -\theta: \quad \text{buy on A, sell on B}.
\]
All of this interacts with an explicit inventory-skew term: if \(q_t^{(i)}\) is our inventory in stock \(i\), tick size is \(\Delta_\text{tick}\), and \(\alpha\) is a skew coefficient, our quoted fair value becomes
\[
\tilde m_t^{(i)} = m_t^{(i)} - \alpha\, q_t^{(i)}\, \Delta_\text{tick},
\]
pushing quotes away from our current position so that the market naturally helps us mean-revert towards flat.

Finally, once the base market making and arbitrage layers were stable, we added a lightweight sentiment-trading overlay. Each news message is mapped to a ticker and a sentiment score \(s_t \in [-1,1]\). Positive sentiment nudges the fair value and inventory target upwards, negative sentiment downwards, effectively adding a directional alpha term on top of our neutral market-making engine while still respecting the same risk and inventory controls.
