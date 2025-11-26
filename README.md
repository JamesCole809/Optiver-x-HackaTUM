# Optiver-x-HackaTUM
Write up for the Optiver x HackaTUM challenge 2025.

HackaTUM is a 3-day hackathon with numerous sponsors including Optiver, IMC Trading, Sixt, Logitech, and many others. Originally our team had intended on competing in both the Optiver and IMC challenges at the same time (with previous clearance from the HackaTUM staff), but this rule kept getting changed or removed for us over the first 24 hours, making our progress in both challenges inefficient due to constant rule changes. On the second day we decided to primarily focus on the Optiver challenge in order to not waste any more time.

## Introduction

This project was built for the HackaTUM 2025 Optibook challenge. The setting is a simulated electronic market with five stocks, each dual-listed on two venues and traded against other teams as well as built-in bots. In addition to the limit order books, a live news channel publishes stock-specific and global headlines that can move prices. Our goal was to design an automated trader that starts as a robust, neutral market maker and then layers in news-awareness, cross-listing arbitrage, inventory control, and finally sentiment-driven directional trading.

Our first step was a mid-price market-making engine that tries to capture the spread as often as possible. For each order book we compute a VWAP mid price from both sides

$$
\text{VWAP}_{\text{bid}} =
\frac{\sum_i p_i^{(b)} q_i^{(b)}}{\sum_i q_i^{(b)}},
\qquad
\text{VWAP}_{\text{ask}} =
\frac{\sum_j p_j^{(a)} q_j^{(a)}}{\sum_j q_j^{(a)}},
$$

and define

$$
m_t = \frac{\text{VWAP}_{\text{bid}} + \text{VWAP}_{\text{ask}}}{2}.
$$

Around this fair value we quote a symmetric half-spread $s_t$,

$$
b_t = m_t - s_t, \qquad a_t = m_t + s_t,
$$

so that a round-trip buy at $b_t$ and sell at $a_t$ locks in

$$
\text{PnL}_\text{spread} = a_t - b_t = 2s_t
$$

per share, as long as we stay passive and within our position limits.

The news feed is then used to adapt both our risk and our pricing. Stock-specific headlines widen the half-spread for the affected instrument, while global headlines widen spreads for all stocks for a short window. Conceptually, our effective half-spread becomes

$$
s_t^{\text{eff}} =
\max\!\left(
s_{\text{base}},
s_{\text{stock}}(t),
s_{\text{global}}(t),
\frac{1}{2}\bigl(a_t^{\text{book}} - b_t^{\text{book}}\bigr)
\right),
$$

making us more conservative precisely when uncertainty is high, but still allowing us to tighten up when the book itself is tight.

With five dual-listed stocks, the next layer is cross-venue arbitrage. For stock $i$ we track mid prices $m_t^{(i,A)}$ and $m_t^{(i,B)}$ on venues A and B and define the mispricing

$$
\Delta_t^{(i)} = m_t^{(i,A)} - m_t^{(i,B)}.
$$

Whenever $|\Delta_t^{(i)}| > \theta$ for a threshold $\theta$ that covers fees and noise, we trade the spread by buying the cheap venue and selling the expensive one:

- if $\Delta_t^{(i)} > \theta$: sell on A, buy on B;
- if $\Delta_t^{(i)} < -\theta$: buy on A, sell on B.

All of this is controlled by an explicit inventory skew. Let $q_t^{(i)}$ be our inventory in stock $i$, $\Delta_{\text{tick}}$ the tick size, and $\alpha$ a skew coefficient. We shift our fair value by

$$
\tilde m_t^{(i)} = m_t^{(i)} - \alpha \, q_t^{(i)} \, \Delta_{\text{tick}},
$$

which pushes our quotes away from our current position and encourages the market to help us mean-revert back towards flat.

Once the base market-making, news reaction, and arbitrage layers were stable, we added a final sentiment-trading overlay. Each news item is mapped to a ticker and a sentiment score $s_t^{(i)} \in [-1,1]$. Positive sentiment nudges the fair value and inventory target upward, negative sentiment pushes them down, effectively adding a small directional alpha term on top of our otherwise neutral market-making engine while keeping the same risk and inventory controls.

## Final Notes

Firstly, a massive thank you to the Optiver team for organising and running this challenge and we look forward to competing (and hopefully winning) next year. I'd also like to thank the hosts of HackaTUM for the large amount of effort and logistics required to run such an event.
