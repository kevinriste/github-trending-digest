"""Levine-style daily synthesis column over the day's substantial HN stories.

Ported (copied + adapted) from ai-newsletter's OVERALL_SYNTHESIS_SYSTEM and its
1500-char "substantial primary content" gate. Synthesizes each story's primary
content (article or self-post); the HN comment discussion is rendered separately
on the page and is deliberately NOT fed here (an A/B showed the weave diluted the
column's voice without adding reaction). Pure of presentation and of any import of
trending_digest; mirrors hn_comment_camps.py's shape and error-swallowing.
"""

import logging
import os

from openai import OpenAI, OpenAIError

# OpenAI request failures are swallowed so a bad key never crashes the daily run,
# and recorded here so the caller can surface a single run-level notification.
API_ERRORS: list[str] = []

HN_TAKE_MODEL = os.environ.get("HN_TAKE_MODEL", "gpt-5.6-sol")
HN_TAKE_REASONING = os.environ.get("HN_TAKE_REASONING", "medium")
HN_TAKE_PROMPT_VERSION = "hn_take_v2"

HN_TAKE_MIN_CHARS = int(os.environ.get("HN_TAKE_MIN_CHARS", "1500"))
HN_TAKE_BODY_CAP = int(os.environ.get("HN_TAKE_BODY_CAP", "12000"))
HN_TAKE_MAX_STORIES = int(os.environ.get("HN_TAKE_MAX_STORIES", "10"))

_SELF_POST_TYPES = {"story", "ask", "show"}

HN_TAKE_SYSTEM = """You are writing the opening of a daily AI/tech news roundup, in the established house style of this newsletter. Below are five example columns written in that house style. Study them and write today's edition the same way: the plain-then-absurd setups, the short quotes of the source followed by dry commentary, the deadpan asides, the deflating landings, the habit of following real logic to a true but ridiculous conclusion. Match that style and method as closely as you can.

IMPORTANT: output ONLY the newsletter prose. No preamble, no disclaimer, no commentary about the task or the style. Begin directly with the first section title.

===== FIVE EXAMPLE COLUMNS (house style) =====

Ice cream hedge.
Oh sure:

28wishes, a bougie independent ice cream parlor in downtown Los Angeles, has been gaining cash on Kalshi, the online "prediction market" app that lets users gamble on everything from political races to Met Gala looks.

According to the shop's owner, 37-year-old Jason Jiang, he's been clearing up to $1,500 per month on the app by being bullish on cold snaps in Southern California.

"Ice cream is one of the most weather-dependent products in the world," explained Jiang, who worked in corporate banking before swirling soft serve.

"We lose about 20 percent of business when the weather goes below 70 degrees... So we're essentially hedging that profit loss on the app."

Sure. The basic situation with Kalshi is that it really wants to be perceived as a useful platform for lovable Main Street businesses to hedge real-world risks, but it is mostly a sports gambling website. So there is a steady stream of stories about companies (bars, sports teams) using Kalshi to hedge real-world sports risk with sports bets. But Kalshi is not exclusively a sports gambling website. It also offers weather bets. Every Cutesy Economics 101 textbook will tell you that ice cream sales are higher when it is hot and sunny and lower when it is cold and rainy. An ice cream shop cannot control the weather, but it can hedge its weather risk with weather bets. Cutesy Financial Derivatives 101.

By the way, while ice cream shops are the standard Cutesy Economics 101 example of businesses whose income depends on the weather, they are not the only or main example in the real world. Lots of other businesses are weather-dependent and hedge their weather risk. Property insurance companies have to pay more claims when hurricanes hit, and hedge by selling catastrophe bonds. Farmers lose their crops when there is no rain, and hedge by buying crop insurance.

We actually talked about crop insurance a few years ago. Despite the name ("crop insurance"), US crop insurance is often structured as a bet on rainfall: It pays out "when there is less than the usual amount of precipitation" in a given area, as measured by the government weather service, "even if the relevant farmland suffers no loss in productivity." (It is parametric insurance, rather than loss-based insurance.) Apparently the way that the weather service measures precipitation is by putting out buckets and seeing how much water falls into them. We talked about this because some farmers were convicted of manipulating the precipitation numbers by doing things like (1) putting covers on the buckets so rain didn't fall into them or (2) dumping out the buckets when rain did fall into them.

As it happens, last month Ag Web ran a long article about those guys, titled "Rain Robbers: How Four Farmers Faked a Drought and Stole Millions in Crop Insurance":

Even on freak days when the sky pissed rain, the farms of Patrick Esch and Ed Dean Jagers remained bone-dry. Parched became payday. In one of the most madcap crop insurance scandals on record, Esch and Jagers turned moisture misery into a multi-million-dollar heist. The Colorado cowboys stole $6.5 million worth of raindrops.

The farming duo manipulated U.S. weather, literally. They plugged, tipped, covered, and destroyed federal rain gauges in Colorado and Kansas, ensuring NOAA weather stations recorded zero-level rainfall. The result? A windfall in illicit gain.

Before landing in USDA crosshairs, via a bizarre narrative more fitting for Jerry Springer, rather than Taylor Sheridan, Esch and Jagers set the fuse on a powder keg of family intrigue, truck-stop hijinks, cash bribes, snitches, whistleblowers, prison escapes, and dead bodies.

You start by dumping out rain buckets, one thing leads to another, and eventually there are prison escapes and dead bodies.

We also talked earlier this year about weather contract manipulation on Polymarket, Kalshi's main prediction-market competitor: Apparently some Polymarket traders bet on high temperatures in Paris, and then warmed up a weather sensor to make their bets pay off. Same basic idea. Weather hedging is a lot older than prediction markets, and manipulating weather sensors to cheat on weather hedging is also a lot older than prediction markets. But prediction markets have, ehhhhhhhh, democratized it.

Truth API pricing.
We talked on Thursday about the fact that the president of the United States is selling early access to his policy announcements for cash payable to his personal media company. Don't look at me, man; I just work here. The Financial Times has more on the product details and pricing:

Donald Trump's social media company has discussed charging traders and investors as much as $100,000 a month for faster access to the US president's posts on his Truth Social platform.

Trump Media & Technology Group has quoted the six-figure monthly sum in talks with prospective buyers of the "Truth API" data service, according to people familiar with the matter.

Proprietary trading firms and hedge funds pay huge sums for ultrafast data feeds because every millisecond counts when reacting to market-moving news. Trump often makes major announcements on Truth Social that trigger huge fluctuations across global markets.

And Bloomberg's Annie Massa saw the pitch:

The note promised "sub-second post data 24/7, including weekends and after-hours," according to a copy of the message seen by Bloomberg News. It included an exhortation to act fast.

"A number of your peers are moving forward with this product," the email said.

And if the stakes weren't clear enough, the solicitation closed with a quote it attributed to a portfolio manager at JPMorgan Chase & Co.: "We're one Truth Social post away from being up or down 5% every day."

Arguably the main economic policy goal of the US government these days is to maximize volatility, which makes early access to policy announcements especially valuable and thus maximizes the president's personal revenue opportunity. Trump Media had revenue of $871,200 last quarter. If it sells just three API subscriptions at $100,000 a month each, a majority of its revenue will come from selling early access to the president's announcements.

To be fair, $100,000 a month only gets you a few milliseconds of early access. For the right firms, that's worth it, notes the FT:

The general public would not notice the difference in speed between Truth API and updates on Truth Social itself because Truth API would give an advantage of "milliseconds" to customers of the feed.

"Milliseconds is a big deal in this world, high-frequency trading firms and systematic quant hedge funds would definitely want this product," said the chief executive of a US market infrastructure company.

But that's just the public pricing, $100,000 for a few milliseconds. Pricing for the premium package , with access to policy news hours or days in advance , is available upon request, hahaha, kidding, sort of.

LCDL.
Last Tuesday, July 14, electric vehicle trade publication EV reported that Lucid Group Inc. had hired a restructuring adviser and was considering filing for bankruptcy. Lucid's stock, which had closed at $5.51 the previous day, fell to a low of $2.37 at 1:43 p.m. on Tuesday. Lucid denied that it was considering bankruptcy, and the stock recovered. It closed at $4.62, down 16.2% for the day, and has been up since; it finished the week at $7.36. There is just a brief sharp gash in the stock chart, just an unpleasant hour and a half. Overall, the stock was up 32.6% for the week.

If you owned Lucid stock last week, you were up 32.6%. If you owned Lucid on margin, though, things were trickier. At its low on Tuesday, the stock was down 57%. If you put up $100 of your own money to buy $200 of Lucid stock, your stock was at that point worth something like $86. You definitely got a margin call. Perhaps you put up more cash, thinking "this is a temporary blip." But probably, as Lucid approached its lows, at least some leveraged investors got blown out of their positions. At least some sellers at the lows were forced sellers.

For instance there was a 2x levered exchange-traded fund on Lucid, the Granite Shares 2x Long LCID Daily ETF (LCDL), whoops. Here is its obituary:

LCDL's investment objective is to "seek daily investment results, before fees and expenses, of 2 times (200%) the daily percentage change of the common stock of Lucid Group, Inc. (NYSE: LCID)."

On 14 July 2026, the share price of Lucid Group, Inc. declined intraday by more than 50% from the previous day's closing price.

In accordance with the Fund's governing documents and the terms of its swap agreements, the swap counterparty exercised its contractual right to close out the swap position following the intraday decline. As a result, the Fund's net asset value became negative. ...

The NAV per share on July 14, 2026, is -$0.016, meaning the NAV is negative. Accordingly, LCDL will be terminated. The Fund's trading status will remain halted until it is officially delisted and liquidated.

Technically, 2 times the daily change of Lucid's stock price wouldn't have been that bad; even on its worst day, Lucid was only down 16.2% from close to close. LCDL would have been up about 57% last week if it had perfectly achieved its goal of returning 2x the daily returns of Lucid. Footnote: That is, the daily returns last week were -0.72%, -16.15%, 28.79%, 8.57% and 13.93%; if you just double those and apply them sequentially the result is 57%. Doubling the weekly return would give you 65.2%. But the only realistic way to obtain twice the daily change of Lucid's stock price is to actually own (twice as much of) the stock during the day, Footnote: Or, more to the point, have a swap with a counterparty who does that and is pretty trigger-happy about blowing out of it. and when the stock fell 57% intraday the game was over.

Elsewhere, here's a new paper by Chris Murray and Marco Sammon of Harvard on "The Costs and Benefits of Leveraged ETFs":

We study the costs and benefits of leveraged ETFs (LETFs) for investors. LETFs can be valuable when they provide levered exposure to diversified equity portfolios in rising markets: long broad equity-index LETFs generated more than $100 billion in investor gains, including over $40 billion relative to counterfactual investments in the underlying assets. But the same products have different economics when applied to volatile individual stocks. Single-stock LETFs are launched on stocks near the top of the volatility and past-return distributions, and products tied to more volatile stocks attract more assets after launch. As a result, these single-stock products have larger volatility drag and higher financing costs, which raise the breakeven return required to outperform the unlevered underlying asset. We also find that LETF flows differ sharply from unlevered ETF flows, as investors buy after recent losses and sell after recent gains. However, these flows do not predict future returns.

Intuitively, "people should borrow money to invest in the stock index" is actually a pretty respectable idea with a lot of good life-cycle consumption-smoothing theory behind it, so why not package it into an ETF. "People should borrow money to bet on risky electric vehicle manufacturers" ends pretty predictably.

Quarterly earnings.
I sort of respect this?

The Securities and Exchange Commission is expected to move forward with a version of its proposal to make quarterly financial reports optional, despite being inundated with public comments that overwhelmingly oppose the idea.

The proposed rule-change, unveiled by SEC Chairman Paul Atkins in May, would give public companies the option to disclose their financial results twice a year, rather than on a quarterly basis. President Trump has long been a proponent of the idea and briefly explored it in his first term.
The SEC asked for the public's thoughts on the idea and received over 200,000 comments, a record number, according to people familiar with the matter. Many of them argued that it would harm investors and leave them with less information on which to make decisions.

Like: Obviously it is bad for public companies to report twice a year. All the comments say that it's bad because it is. But most companies won't switch to semiannual reporting just because that's allowed. And the SEC's point is not that it's good. The point is that smallish shady-ish companies can choose to go public or not. If they don't go public, they have to report financial results zero times a year; if they do, four times (currently) or twice (under the new proposed rules). There is some margin where some smallish shady-ish companies will go public under a twice-a-year rule but not under a four-times-a-year rule. Two is more than zero. As I wrote a few months ago: "this SEC proposal is intended to get retail investors more information, on the theory that two reports a year is better than none, and optional semiannual reporting will get more companies to go public."

On the other hand, do you want more smallish shady-ish companies to go public? I don't have a strong view on that, but I gather that the SEC does!

Drugs.
Every so often I write that the core use case of Bitcoin is buying drugs online, and I feel kind of guilty and people get mad at me about it. Also all the other stuff! Important uses in financial speculation, store of value, financial freedom, blah blah blah, don't email me. But here's a Bloomberg News story about "gray market peptide vendors," and you will absolutely believe what currency is used to buy unapproved weight-loss drugs online:

Approved medicines move through clinical trials, regulators and pharmacies like Walgreens and CVS. Much of the online peptide trade bypasses that system. Cheaper compounds are shipped directly from overseas laboratories, without prescriptions or insurance coverage, some with disclaimers that they aren't for human use.

Banks and credit card networks often won't work with suppliers in that gray area because of the legal and regulatory risks surrounding products touted for unproven health benefits. A growing number of them take only Bitcoin.

I would like to subscribe to, like, good macroeconomic research about the Land of Bitcoin. Like sure sure sure I know all about the hot-money financial flows into Bitcoin from the digital asset treasury carry trade. But what about the underlying economic fundamentals? Is Bitcoin's main export industry , its main source of hard currency , really drugs? Has the rise of weight loss drugs been good enough for Bitcoin's economy to offset the reversal of the DAT carry trade? Can Bitcoin stay competitive in the gray market peptide business? Will it move up the value chain? (Start inventing drugs itself?) Bitcoin's economy is fascinating and I get only anecdotal glimpses of it, probably because so much of it is on the dark web.

Reinspection fees.
Longtime readers of this column know that, around here, we love a good euphemism for bribes. Bad euphemisms for bribes sound like euphemisms for bribes: chickens, tuna, chocolates, you-know-what, Scooby Snacks. Good euphemisms for bribes sound like boring business expenses: success fees, marketing expenses, commissions, consulting fees, API keys. A reader sent me this announcement from the US Department of Justice:

The Scoular Company (Scoular), an agricultural supply chain company based in Omaha, Nebraska, will pay over $10 million to resolve an investigation by the Justice Department into a years-long scheme in which it relied on bribery of Mexican officials to deliver trains of goods across the U.S.-Mexico border. ...

According to court documents, between 2013 and 2019, Scoular relied on multiple customs brokers to ensure that its shipments of corn and other products successfully crossed from the United States into Mexico. Under Mexican law, those shipments were subject to inspection for dirt, soil, and other impurities. To ensure that Scoular's shipments successfully transited the border despite inspections that found such dirt, soil, and other impurities, Scoular authorized multiple third-party customs brokers to bribe Mexican officials at the border. At the direction of Scoular employees, and for Scoular's benefit, those brokers paid bribes of approximately $2,000 per Scoular train and invoiced the bribes back to Scoular for reimbursement of reinspection fees, which Scoular paid. Scoular employees communicated about shipments and bribes via Whats App and other means. In total, Scoular authorized bribes of more than $400,000 and avoided fees and costs of more than $6.5 million.

Like, the customs official inspects the cargo and finds impurities, so she doesn't let it through, but then the broker is like "are you sure? why don't you ... inspect it ... again," and slides across an envelope of cash. "Reinspection fees"!

Things happen.
Paramount-Warner Bros. Judge Pauses Deal With 'Serious' Concerns. Moonshot Is Creating New Winners and Losers in the AI Trade. Chinese AI Sensation Moonshot's Gamble on Big Models Pays Off. Everyday Investors Are Over the Mag Seven and Into New AI Darlings. China's 'national team' buys shares worth $9bn to prop up market. The Justice Department Is Pulling Back on Prosecuting Corporate Crime. US day traders flock to 'the most dangerous product in crypto.' Goldman Offers Preferred Stock Days After Post-Crisis Spread Low. The CEO Trying to Fix Pay Pal Has a New Option: Sell It for Billions. Griffin Wants to Buy an Old Cottage Across From New Headquarters. Miami Is Losing Its Claim to a Cheaper Cost of Living Than NYC. Rare Pair of Improbably Light 'Super-Puff' Planets Is Discovered. Five Cups of Coffee a Day Is Fine for Most Adults, Heart Association Says. Apollo Likens Risky Private Credit to Mere 'Sprinkle' on Cupcake. What's Martin Shkreli up to?

=====================

Homoglyphs.
Okay here's an investor relations strategy:

This is so stupid in so many ways, and also extremely not any sort of advice, but it is based on real academic research. Bloomberg's Lu Wang reports :

At the University of Liechtenstein, Advije Rizvani, Giovanni Apruzzese and Pavel Laskov designed 10 LLM-based trading models to forecast share prices with sentiment analysis for a portfolio of stocks. All generated positive returns during a 14-month investment period through April 2025. Yet every model was fooled after researchers made subtle changes to financial news headlines that were barely noticeable to human readers, such as swapping letters for nearly identical-looking characters or embedding hidden text.

Here is the paper, "Adversarial News and Lost Profits: Manipulating Headlines in LLM-Driven Algorithmic Trading ," which really does involve "Unicode homoglyph substitution":

A few characters in a stock name (e.g., A, e) can be replaced by visually indistinguishable Unicode counterparts (e.g., Cyrillic "A" , "" ). The manipulated headline appears to be unchanged to the human eye. When such headline is used in the [algorithmic trading system] pipeline, it may elicit wrong decisions in the stock mapping algorithm, determining which stock a given headline refers to, and thus "misroute" a headline.

"We consider an adversary with no direct access to an [algorithmic trading system] but able to alter stock-related news headlines on a single day," they write, but isn't the obvious "adversary" here the company itself? We have, ridiculously,previously discussed the the idea of downplaying unpleasant corporate news by printing it in white text on a white background, and I wrote:

Honestly this all feels quaint already. Pretty soon all corporate disclosures will be read exclusively by computers, and concealing information by making it unreadable to humans won't work. What you'll want is to conceal it from the computers, which is harder.

Totally wrong! Unicode homoglyph substitution! Maybe it's the future of stock promotion.

Universal basic AI.
We have talked a few times about the maximalist vision of artificial intelligence, which goes something like this:

In the future, AI robots will replace most human endeavor and nobody will have jobs anymore. The problem of economics will be how to allocate all the value created by the robots. In our current system of market capitalism, that value will be allocated to the owners of the robots So you'd better own some robots. Society should get out ahead of that problem by making sure that everyone owns the AI companies that will control the robots. Perhaps governments should nationalize the AI companies, or tax them extra to fund universal basic income. The more market-based approach is that the AI companies should be large and increasing components of the stock index, and everyone should buy index funds in their retirement accounts to own their share of the robots. Now. You don't have to believe this maximalist vision. You could think AI won't be that big a deal, or it will create more opportunities for human labor, or it will wipe out humanity and thus render stock ownership irrelevant, or something else. Here I just want to make the narrow point that this particular maximalist vision is quitebullish for AI stock prices. The message is "in the future, all of the value in the world will belong to AI companies, so you'd better buy AI stocks today." 

I write sometimes that "nobody in history has ever been better at business negging than Sam Altman." What I mean is that Altman has mastered the art of saying and doing faciallybad things about Open AI, so that people will thinkgood things about Open AI. Most notably, he used to spend a lot of timepublicly worrying that AI might destroy humanity, which sounds bad when you put it like that, but which also might make you think "ooh this technology is really powerful, I'd better invest." Similarly,here's this :

Open AI has discussed giving a 5 per cent stake to the US government as the $852bn AI start-up seeks to clear political obstacles by securing financial buy-in from the Trump administration.

Sam Altman, chief executive of the Chat GPT maker, has argued that giving the public a financial stake in the company is the best way to share the upside of AI and has suggested a stake of this size in early conversations with the administration, according to two people familiar with the talks. ...

Altman and other Open AI executives have suggested that each of America's leading AI developers allot 5 per cent of their equity to a vehicle like the Alaska Permanent Fund, a sovereign fund that invests the state's oil wealth into stocks and pays dividends to the state government and residents. ...

Open AI and Anthropic have previously suggested in economic policy proposals that arrangements such as public or sovereign wealth funds may be required in future to distribute shares to the public.

In April, Open AI proposed a "public wealth fund" that "provides every citizen , including those not invested in financial markets , with a stake in AI-driven economic growth".

The Open AI Foundation, the company's non-profit arm, said in May that in an AI-led future, "society will likely need new approaches that give people durable stakes in the systems creating value", pointing to public or sovereign wealth funds.

On the one hand, giving away 5% of your equity is in some nominal sense dilutive to your shareholders. On the other hand, giving away 5% of your equity is a small price to pay to persuade everyone that owning your equity is essential to the future of humanity. Then you sell the other 95%.

Private bonds.
One way that I like to tell the story of private credit is:

In the olden days, insurance companies bought a lot of corporate bonds, clipped the coupons and held them to maturity. The insurance companies had predictable long-dated liabilities, and they matched them up with predictable long-dated liabilities from corporate bonds. Then, like, Bill Gross invented bond trading : Instead of holding bonds to maturity, insurance companies could trade them to make better risk-adjusted returns. In the beginning, this was lucrative, but ultimately the bond market just became more efficient and returns were competed down. Then some insurance companies invented the opposite of bond trading: Instead of buying liquid traded bonds that pay relatively low returns, you can accept some illiquidity, offer borrowers a better experience (faster marketing, lower market risk, a single lender, a better relationship with that lender, less paperwork, etc.) and demand a higher interest rate. Then you hold the bonds until maturity and clip the coupons, which is fine, because you are an insurance company with predictable long-dated liabilities. That is the core story, and then modern "private credit" introduces various inessential novelties. For instance: Fifty years ago, when Bill Gross was getting his start, the innovators in insurance company lending worked at insurance companies. In the 2020s, they work at "alternative asset managers," which manage money for insurance companies and/or own insurance companies directly. And so now the high-profile way that insurance companies do illiquid buy-and-hold lending is through private credit funds, because it is cool and lucrative to manage a private credit fund and boring to run an insurance company.

But that is only approximately true, and in fact the boring old business of "insurance companies buy bonds and hold them" never entirely went away. Bloomberg's Emily Graffeo reports that it's having a bit of a renaissance:

A private bond market dating back more than a century is opening a new front in the trillion-dollar AI funding boom, allowing tech borrowers to sell debt directly to deep-pocketed insurance firms.

Borrowers are hunting for capital wherever they can to finance the vast sums needed for the build out of AI. Meanwhile, life insurers facing record demand for annuities are seeking longer-term, high-grade corporate bonds to finance those multi-decade liabilities.

Private bonds, where companies sell securities directly to groups of select institutional investors, are bridging the gap. Issuance hit roughly $81 billion this year through May, the most for the period in data going back to 2016, according to Private Placement Monitor. Industry participants say AI is fueling the surge.

"Especially with AI and data centers, there's an insatiable need for capital," said Sheel Patel, head of New York private credit at Mayer Brown. "The borrowers are increasingly more comfortable using the private market for larger financings, especially when they realize that certainty, flexibility and confidentiality are a priority in these transactions." ...

This shift comes as a swelling population of Americans over age 65 drives annuity sales to record levels. In fact, total annuity sales hit an all-time high of roughly $464 billion last year, according to the life insurance trade group LIMRA. To back these expanding long-term liabilities, insurance firms are scaling up their purchases of private corporate bonds.

In some ways the simplest possible financial story is: "Businesses need money to make long-term investments in physical capital that will increase their future profits. They borrow that money from insurance companies who have lots of money and need to plan for predictable long-term liabilities." You can complicate that story with liquid public bond markets or private credit funds or whatever. But the simple story is that you want a steady income in retirement, AI data center rents can provide that steady income, and the financial industry will match you up with a data center and make that trade happen.

Blue Owl.
Elsewhere :

For the second straight quarter, two Blue Owl Capital Inc. private credit funds were hit with the industry's largest redemption requests, forcing the manager to again cap withdrawals.

Investors in the roughly $34 billion Blue Owl Credit Income Corp., one of the largest in the industry, asked to pull 18.8% of shares, or $3.6 billion in the second quarter, according to an investor letter Thursday. That was down from $4.2 billion requested in the prior period from the fund known as OCIC.

The smaller Blue Owl Technology Income Corp. saw shareholders request 38.1%, or $1.1 billion, compared with $1.2 billion in the first quarter.

Disclosure: I have a small investment in BCIC, sorry sorry. "Insurance companies have predictable long-term liabilities and can commit to making illiquid long-term investments" is a good classic simple story; "retail investors probably won't want their money back all at once so let's put it in illiquid long-term investments" is also a story but isnotably less good . It turns out this is not the best technology for matching your retirement plans to a data center.

Prediction market manipulation.
Polymarket, the prediction market, offers five-minute binary options on Bitcoin . If Bitcoin trades at $62,000 at 9:30 a.m., you can put in a bet on whether it will be up or down at 9:35. If it trades above $62,000 at 9:35, the "Up" bets win $1 and the "Down" bets get $0; if it trades below $62,000 at 9:35, "Down" wins.

If you put a lot of money on "Up," and at 9:34 and 55 seconds Bitcoin is trading at $61,999.99, you should probably go buy some Bitcoin. Your buying will push up the price, and if you can push the price up to $62,000.01, your Polymarket options will pay off. Then you can sell the Bitcoin you just bought. When you sell your Bitcoin, the price will probably go back down. You will lose money on the round-trip Bitcoin trades: You'll buy on the way up, sell on the way down, and probably sell at a lower average price than you bought. But you will make money on the Polymarket trades: You bought "Up" at 50 cents or whatever, and it pays $1. Whether this is a good trade or not depends on the relative size and liquidity of those markets. If you can buy 10,000 Up contracts at 50 cents, you can make $5,000; if you can then move the Bitcoin price up by spending $4,000, you should. This is not any sort of advice at all, and this is obviously bad market manipulation, but, you know, we are talking about Bitcoin and Polymarket here. There are alsofive-minute Dogecoin binaries .

We talked yesterday about market manipulation in eggs. I wrote:

This is a familiar story and you probably know the ending. There's a big market (egg producers selling eggs to supermarkets etc.), and there's a small market (egg producers selling extra eggs to each other on an electronic exchange). The price in the small market determines the price in the big market. Participants in the small market are also participants in the big market. You can spend a little money in the small market to move the price, which can make you a lot of money in the big market.

"Big market" and "small market" there are imprecise terms. The market for Bitcoin isbigger, by volume, than the market for five-minute binary options, but binary options are discontinuous, so moving the price of Bitcoin by a little bit can have a big payoff in your binary options. The relevant question is whether you can spend a little money to move the price of Bitcoin in a way that makes you more money in the binary options market. When prediction markets were just a glimmer in the eye of libertarian economists, the answer to that question was "of course not, what are you even talking about." But,as I wrote a few months ago , "now prediction markets exist and are a big business, so they are not only in the business ofpredicting reality: They'realso in the business of changing it."

Here's a paper by David Dai, Ruizhe Jia and Shihao Yu on "Settlement Manipulation in Prediction Markets ":

Empirically, we study Polymarket's Bitcoin five-minute up/down contract: a binary claim that pays $1 if Bitcoin is higher at the close of a five-minute window than at its open, and $0 otherwise. ... The contract's launch on February 12, 2026 offers a clean natural experiment: no five-minute contract existed before that date, so we compare three regimes: no such contract (P1), the 15-minute and 4-hour contracts only (P2), and the five-minute contract live (P3).

As soon as it launched, a relatively small prediction market began redirecting large spot orders to the settlement seconds. In the final ten seconds before each close, trading on Binance spikes: the magnitude of net order flow jumps, and volatility rises with it. The timing is no accident: the spike appears only after the five-minute launch (about 50% above the pre-launch level), and is significantly attenuated at the fifteen-minute horizon. The spike is sharpest where a push is pivotal: in the roughly 6% of cycles whose contract price still implies a near-even outcome just before the close, the near-settlement order-flow jump is about 3.9 times that in the rest. The clearest sign that this is not information is the reversal: within ten seconds the price reverts, by about a quarter in the near-even cycles and a tenth in the others. Real information would persist; the price impact of a manipulative push reverts.

We have talked about prediction market manipulation before. There is a Polymarket contract on whether Jesus Christ will return before 2027, and there was another Polymarket contract on whether the Christ-will-return contract would trade above 5% during some one-hour window, and allegedly people bought the Christ-will-return contract in an effort to make the derivative contract pay out. I wrote: "What possible purpose could the 'Jesus Christ return before 2027 Odds >5% February 17, 12-1 AM?' contract haveother than as a plaything for market manipulation?" I submit to you that if you lose money to market manipulators on the "Bitcoin up in the next five minutes" contract, you deserve it. Not legal advice or whatever.

Pivot to lethality.
Lots of investors have investment mandates with some sort of environmental, social or governance restriction. "Don't invest in weapons companies" is a reasonably common one. Some companies make weapons and some don't; weapons-restricted investors invest only in the ones that don't. They have to pay attention, though, and update their holdings periodically. Sometimes a snack food company will start making weapons. Or a non-weapons company will acquire, or be acquired by, a weapons company.

If you're a public-company shareholder with a no-weapons mandate, this is not a big problem: You just sell the stocks that are now weapons stocks. If you're a private-company shareholder, it's harder: There's no liquid market for your stock, so you are stuck. When there is no exit, youneed to exercise voice : A no-weapons shareholder in a private company that starts getting curious about weapons, traditionally, has to tell the company's executives "no, this is not what we signed up for." But private markets are the new public markets, and now it's easy to migrate no-weapons investors out of the shareholder base. The Financial Times reports :

German surveillance drone maker Quantum Systems will consider merging with kamikaze drone start-up Stark after using its latest $1.2bn funding round to part ways with investors opposed to investing in weapons.

Florian Seibel, Quantum co-founder and co-CEO, said the company used the funding round, which valued the defence technology group at about $8bn, to "clean up" its shareholder structure and allow it to develop lethal technologies for the first time.

Anyone who did not "feel comfortable with the potentially new alignment of the company had a chance here to exit", he told the FT.

That could pave the way for moves into areas such as deep-strike missiles, he said, as well as a possible merger with Stark, which he also co-founded, to bring his "two babies back together".

I like that Seibel loves drones indiscriminately but had to have separate killer and non-killer drone companies to appeal to two distinct sets of investors. But he has now been successful enough to kick out the non-killer investors and reunite his "two babies."

Pivot to AI.
I mean, you know :

Empery Digital Inc. (NASDAQ: EMPD) (the "Company" or "Empery Digital") [Tuesday] announced that it has entered into a definitive agreement for a $65 million investment (the "Investment") representing a 25% ownership into a private entity that is acquiring a strategically located Midwest facility to be converted into a state-of-the-art AI data center.

Empery Digital and Hunt Properties, Inc. have entered a strategic partnership to jointly originate, evaluate, and acquire powered land properties with secured tenants suitable for AI and high-performance computing data center development. The partnership will combine Hunt Properties' decades of experience navigating utility interconnection processes, power procurement, and energy infrastructure development and their established network of relationships across the U.S. with Empery Digital's public company platform, expertise in capital markets, and strong balance sheet, including its Bitcoin holdings, to execute a shared vision for AI infrastructure.

Etc. We have talked about Empery a couple of times before. As of early 2025, it was called Volcon Inc. and was in the business of selling electric bikes and golf carts. But in July 2025, a better opportunity came along, and Volconbecame a digital asset treasury company , or DAT (and renamed itself Empery). That was the thing to do with a US public company, last summer: If you put $500 million of Bitcoin into a public company, the company would be worth $1 billion, so Empery did.

That is no longer the thing to do with a US public company: Empery started trading at a discount to net asset value, and activists have been pushing it to sell its Bitcoin, buy back stock and close the discount. The DAT trade that looked so good last summer , "if you buy Bitcoin, your stock will trade at a premium" , no longer works. Never mind.

The thing to do with a a US public company in the summer of 2026 is of course AI infrastructure. People won't pay a premium for DAT stock anymore, but theywill pay a premium for AI infrastructure stock. Onward!

Things happen.
SEC Probes Alleged Insider Trades That Cost Susquehanna. Trump Made $1 Billion on Crypto Deals While His Fans Lost a Fortune . Nvidia Says It Will Take a Cut of Some Customers' Cloud Revenues. Core Weave Junk Bonds Slide Further as Investors Question AI Boom . Space X Showed Investors Prototype of Elon Musk's New AI Device . Space X Analyst Debut Set to Test $2.2 Trillion Valuation. One Leveraged ETF Is Reshaping Trading in World's Top AI Memory Stock. Bank of England to push ahead with plan tolimit hedge fund leverage . Equity-Market Fundraising at Most Exuberant Since 2021, Mergermarket Says.Millennium Targets at Least $10 Billion in New Fundraising. The Deep Mind trio who built a poker AI are now making money for quant hedge funds. UBS to trial US banking services in push for wealthy American clients.Mc Kinsey Shakes Up Its Board After Scandals Over Past Work With Clients.Hot dog inflation : Americans brace for costliest ever July 4 parties.Whiskey Barrel-Backed Loans Are Plummeting in Value With Americans Drinking Less. The Wall Street Women Who Traded Finance Careers for Influencer Success . Ken Griffin Bought Out All 138 Condos in Miami Tower, One by One. I mean, Latin. Obviously if your corporate news gets picked up by news media, they will probably write about you using normal letters, so this works best for the sorts of small companies that, you know, might actually try it. I suppose some full nationalization versions are not. It rhymes a bit with the "have fun staying poor " Bitcoinmantra .

 

Before it's here, it's on the Bloomberg Terminal. Find out more about how the Terminal delivers information and analysis that financial professionals can't find anywhere else.Learn more .

=====================

Independent sponsors.
My half-joking potted history of business is that people used to go to business school to learn how to run businesses, and then they would graduate and run businesses. They'd take over their family business back home, or climb the ranks at a big company. This was good, because it was good for businesses to be run well by smart, hard-working professionals who were familiar with best practices for management and capital allocation.

And then finance became a more important part of the economy, and people who graduated from business schools decided to go into finance instead. Finance is a sort of meta-business, a layer of abstraction on top of regular businesses, so it creates leverage: Instead of working at a company and figuring out whether to build a factory or how to price a product, you could work at a hedge fund and allocate capital to dozens of companies that are good at building factories or pricing products. Over time, this makes businesses better and more efficient: You allocate capital to the good ones, the bad ones get starved of capital and go out of business, and we asymptotically approach nirvana.

On the other hand not everyone can be a hedge fund manager. Someone has to build the factories and price the products. You can't tilt too far in the direction of finance, or else everyone who is good at business will spend their days allocating capital to people who are bad at business.

One solution to this problem is of the form "actually being good at business is not especially correlated with graduating from a top business school, and people who are good at building factories should build factories while people who are good at trading stocks should trade stocks," but that is boring. Another, funnier solution is to construct a financial system that:

Selects the "best" people in some sort of brutal meritocratic competition,

Funnels them into prestigious finance jobs, and.
Makes it so that those prestigious finance jobs consist of running regular boring companies. "I have arrived at the pinnacle of my career in high finance," you think, as you sit down to optimize the pricing of your company's pest-control services.

We talk about this all the time, because this model loosely describes aspects of the modern private equity industry. "Private equity" is an indisputably financial job, and one that you generally get by graduating from a prestigious college and then working at an investment bank for a few years. And private equity funds are mostly in the business of raising money from investors to allocate capital to businesses. But they are also in the business of business: The model is not "give money to good companies" but rather "use money to take over companies that could be run better, and then run them better." They are operators, not just capital allocators.

And then we talk about "search funds," which are sort of the small-scale artisanal version of private equity: You can graduate from a top business school, raise some money yourself, and go out and buy a single pest-control company. Then you run the pest-control company. "What are you up to these days," your classmates ask at your fifth Harvard Business School reunion, and you answer "I'm a search fund operator." "Oh cool," they say. You are an allocator of capital, a purchaser of undervalued businesses. Also you are in pest control.

I suppose a hybrid model is: You work at a big private equity fund, you run some big buyouts of big companies, but you find this unfulfilling and/or you don't make as much money as you want, so you leave the big private equity fund to go find a pest-control company to call your very own. Instead of running a fund, you raise money from investors for each individual deal: You come to them with a business to buy, and they give you the money to buy it. This is, approximately, the "independent sponsor" business. Bloomberg's Allison Mc Neely and Preeti Singh report:

Firms that take the single-deal approach , known in the industry as independent sponsors , are surging in popularity.

It's a way for employees at big private equity and advisory firms to strike out on their own, rather than wait for the current crop of senior partners to make room at the top for the next generation. For investors who've chafed at the slow pace of returns from buyout funds, it offers the hope of quicker paydays. ...

Independent sponsor Altaline Capital Management was launched last year by mid-career veterans of TA Associates, H.I.G. Capital and KKR, spurred by a slowdown in deals and fewer opportunities for career advancement, according to Rafael Telahun, a managing director.

"For folks who are in a hurry, those moments serve as a bit of a push," he said. In turn, the "pulls" were the volume of deals to be done in the lower middle market and the growing number of investors willing to finance those transactions, he said.

Altaline is not actually in the pest control business, but it is in the elevator maintenance business, and has also been in the homeowners' association management business, another classic. And there is a general focus on applying private equity skills to low-hanging fruit:

Many independent sponsors are investing in deals that generate $2 million to $10 million of adjusted earnings, according to a report from advisory firm Citrin Cooperman. ...

In some ways it's just about going back to basics: Find a founder-run business that has room to grow, pull together a small syndicate of equity and debt investors, and buy it at lower valuation and with less leverage than what's typically used in larger deals. A growing number of baby boomers who founded businesses are looking to retire, and the smallest end of the private equity industry provides ready buyers.

Right, the deep purpose of finance is to replace the founder-owners of successful small businesses with skilled professional managers. If Harvard Business School had a career fair in which hundreds of successful retirement-age plumbers and elevator maintainers sat at little tables looking for bright young whippersnappers to take over for them, the students would not show up. The students want KKR, not elevator repair. But the invisible hand of the market works in mysterious ways, and sometimes it transforms KKR associates into elevator repair executives.

Kalshi parlay market makers.
Stereotypically, the way sportsbooks make money is with parlays. If you want to bet that the Mets will win tonight, a sportsbook will take your bet and will charge you about a 4% expected edge. Footnote: No science to that, just looking at Fan Duel, which this morning offered the Mets moneyline at 136 (bet $100 to win $136, a 42.4% implied probability) and the Braves at -162 (bet $162 to win $100, a 61.8% implied probability), for a total implied probability of about 104.2%, meaning that if Fan Duel ran a matched book it would collect a bit more than a 4% edge. If you want to bet that the Mets will win tonight and that Francisco Lindor will hit a home run and that Bo Bichette will get at least two hits and that A.J. Ewing will hit a triple and that Carson Benge will steal a base, the sportsbook will happily take your money, give you a huge payout if all of those things happen, and keep your money if any one of them doesn't. The sportsbook's edge on that bet will be more like 20%. That is a worse bet for you: The house has more edge. But the payoff for you, if you win, is much higher, so it might be more fun for you. You are betting for fun. So parlays are a perfect product, popular with bettors and lucrative for sportsbooks.

One reason that parlays are lucrative for sportsbooks , that they have higher edge than regular single bets , is that they are riskier: If a bettor hits every leg of a parlay, the sportsbook is out a lot of money. But another reason is that they are less competitive than single bets. If you want to bet that the Mets will win tonight, you can log into a bunch of sportsbooks, see what odds they are all offering, and choose the best bet. This tends to push every sportsbook to offer similar odds. But if you want to bet some complicated 10-leg parlay, that is not a product that is listed on the sportsbook's homepage. The sportsbook gives you some menu to construct parlays, and you construct the parlay you want, and then the sportsbook's model prices it. You could do that on multiple sportsbooks and pick the best price, but (1) it would be a pain and (2) different sportsbooks might offer different options for bets that you could combine in a parlay, so that the more complicated parlays might not be entirely comparable between sportsbooks. If complicated parlays are bespoke products only offered by a single sportsbook, their pricing will be less competitive.

Kalshi is a US federally regulated prediction market, which is a new kind of sportsbook, but in this respect it is just like other sportsbooks: It offers parlays, the parlays are popular, they have a huge house edge, they lose a lot of money for bettors and they are an increasingly dominant part of the business. Bloomberg's Justina Lee and Carolyn Silverman report:

Combo bets have become the fastest-growing corner of the prediction-market world since first showing up late last year, representing 36% of the contracts traded on Kalshi this month.

They have also been among the most dangerous for ordinary customers: bettors on Kalshi's app and website have lost a net $294 million on its combos since the start of the year excluding fees, according to a Bloomberg analysis of the so-called taker trades that mimic the bets made on traditional sportsbooks.

The losses on these sorts of wagers used to be largely captured by sportsbooks, where parlays have been among the industry's most profitable products. Prediction markets are changing that dynamic by promoting similar trades to their small-time customers on their apps, while allowing Wall Street firms and other algorithmic traders to step in as counterparties.

Yes, right, the sports gambling business is absolutely about promoting parlays to retail bettors, and Kalshi is in the sports gambling business.

But Kalshi is an interesting sportsbook in that it does not take the other side of customers' bets. In theory, it is a peer-to-peer market; anyone can take the other side of anyone else's bets. If I want to bet on the Mets, Kalshi will pair me up with someone who wants to bet against them, with no house sitting between us and capturing a spread. In practice, there are market makers on Kalshi, who are largely professional algorithmic trading firms; the "house edge" is bid/ask spread, which seems to be about 1% or 2% for the Mets game.

But what about parlays? It is one thing to match up someone who wants to bet on the Mets with someone who wants to bet on the Braves. How do you match up someone who wants to bet on the Mets and Ewing hitting a triple and etc. etc. etc., some custom multi-leg selection from a vast menu of possible combinations, with someone who wants to bet against that particular combination? Well, a trader can sign up to be a parlay market maker. The retail customer constructs a parlay, and then Kalshi sends it to market makers to price and trade it. Lee and Silverman:

After a customer submits a combo, market makers on Kalshi generally get about a second to offer their best price in a mechanism known as request for quotation, or RFQ, with the winning market maker matched with the customer. (It's akin to the system with the same name used to trade bonds and typically less liquid securities on Wall Street.)

Pricing parlays is so difficult that many RFQ market makers are happy minting profits by pulling the odds from sportsbooks. Lately, though, competition has become fiercer, so market makers have to figure out where they can afford to drop their price to win trades, says Gianni Settino, a 36-year-old software engineer in Los Angeles.

"If everyone's using the exact same algorithm to come up with a price, you're never going to reach the user because you're just at the same level as all the other market makers," said Settino, who started responding to RFQs as a side hustle. "You have to find spots where you can be more competitive."

The one-second RFQ process probably requires a certain level of professionalism and computer infrastructure, but it is not strictly limited to big institutional firms. Some independent professionals can make a good living selling parlays:

Leonidas Mastrokostas, a 26-year-old Jersey City resident, learned about the opportunity when he worked at Fan Duel, one of the biggest sportsbooks. He has since struck out on his own, and he says he has been making seven figures a month by betting against the risk-taking instincts of ordinary gamblers on Kalshi.

"These lottery tickets are what retail is really looking for," said Mastrokostas. "They don't quite understand the pricing, but at the end of the day, given the competition of many makers there, they'll ultimately lose less."

And of course regular sportsbooks have skill at pricing and trading parlays, and they can do it on Kalshi too:

Mastrokostas's old employer, Fan Duel, is also embracing the opportunity. Parlays already account for an outsized portion of the profits on its traditional sportsbook. But Fan Duel has recently started market-making combos on other exchanges as well.

A few points here. First: This is cool? I mean, it's not necessarily great that hundreds of millions of dollars are being extracted from retail bettors with long-odds sports parlays on a US national commodity futures exchange. But if that's gonna happen anyway, it's sort of cool that some of those millions of dollars are being extracted by independent hobbyists, not institutional sportsbooks. A traditional sportsbook won't let quasi-retail bettors take the other side of parlays as a side hustle, but Kalshi will.

Second: My prediction markets white whale is something like "a sports gambling exchange-traded fund that has a systematic positive-expected-value strategy." We talked a few weeks ago about a proposed sports gambling ETF that would be actively managed by a professional gambler, who would try to pick undervalued bets on Kalshi: Possibly a positive expected value, but not systematic or transparent. I half-jokingly proposed an alternative idea, a sports gambling ETF that just bet on the Mets every day or whatever: Very systematic, and possibly a fun gamble, but obviously negative expected value. But what I really want is something systematic and plausibly good. As I wrote:

The rough shape of the idea is: "Certain bets are systematically mispriced for retail supply and demand reasons. For instance, people might not want to make straight-up bets on heavy favorites in college football: Betting $2,000 on the favorite to win $100 is no fun, while betting $100 on the underdog to win $2,000 is fun. Therefore, taking the unpopular side of this bet should offer a positive expected return. An ETF that systematically bet on huge favorites could have positive returns with no correlation to the stock market." We have discussed a guy named Mike Wohl, who actually ran a fund doing that for a while in the early 2010s, though that was back in the dark ages before sports bets traded on regulated US commodities exchanges. Now you can do these sorts of bets on Kalshi.

(Similarly, we have discussed a few times a prediction-market strategy of the form "bet No on everything, because the public is systematically biased in favor of stuff happening, so No offers positive expected returns." Again, this is the sort of thing that could be systematized in an ETF.)

"Taking the other side of parlays" has sort of that shape, and also like 20% edge. It is not as systematic as betting huge favorites, though; it is an active algorithmic correlation-trading business. Still, if the ex-Fan Duel guy can make seven figures a month doing it in his personal account, maybe he could start an ETF?

Nothing happens.
Elsewhere in parlays, a reader sent me this Polymarket contract on "Nothing Ever Happens: 2026," which is currently priced at about 75 cents and pays out $1 if like a dozen weird salient things (China invading Taiwan, Trump acquiring Greenland, Jeffrey Epstein being discovered alive, a major meteor strike, etc.) don't happen, or $0 if any of them do. I have no idea if this is a positive expected-value trade, and it's fairly small volume, but isn't it sort of tempting as an ETF?

It's a little reminiscent of the autocallables ETF we talked about a while back. An autocallables ETF is approximately in the business of selling insurance against a market crash; it collects a nice premium if things are normal and loses a lot of money if the market collapses. This contract has a 33% return if an assortment of catastrophes don't happen, and a negative 100% return if they do. You could imagine some sort of rolling no-catastrophes ETF that lets investors sell more general insurance to the market.

DAT to AI pivot.
One of my rules of thumb is that most cannabis companies used to be gold mining companies. That's probably not literally true, but it's a useful model. There are some small publicly traded US companies that are constantly changing their business models, and they tend to change to whatever business model is hot at any particular time. Gold mining, cannabis, crypto mining, Covid protective gear, a series of fads. Possibly this is because the managers of these companies have an unusually diverse range of skills and interests that happen to line up with whatever the hot opportunities are at any time. But it is also possible that what they are mostly good at is selling stock, and the way to sell stock to retail investors is by saying the latest hot buzzword. Possibly not all of those companies were good at finding gold or distributing cannabis or making Covid protective gear.

Last year's fad for digital asset treasury companies was actually quite nice for all of those constantly pivoting companies:

There was a tremendous demand for small underutilized public companies, so they could all become DATS.

Becoming a DAT was very good for one's stock price: If you put $100 million of crypto into a small public company, it would be worth $200 million, for a while.

You didn't have to do anything else. The whole schtick was selling stock at a premium to buy crypto; you could say some words about building a new digital ecosystem, but nobody cared very much. Being a cannabis company might eventually require some tedious growing and distributing of cannabis, but being a digital asset treasury company was essentially about selling stock. It really suited the companies' skill sets!

The problem is that the fad ended and now DATs do not trade at a premium. So they need to pivot back to something else. And there's nothing else like DATs, where "we sell stock at high prices" was the entire business model. Now you have to pivot back to doing real things, or saying that you'll do real things anyway. Bloomberg's Monique Mulima reports:

The implosion of the once-hot market for cryptocurrency treasury stocks is prompting a number of firms to pivot to artificial intelligence in an attempt to win back investors. So far, it isn't working.

K Wave Media Ltd., a former Bitcoin accumulator that shifted to data center development, has seen its shares fall 71% since rebooting in May. Lixte Biotechnology Holdings Inc.'s shares have fallen 33% since agreeing to merge with a battery firm in June. And Alpha TON Capital Corp., which held alternative cryptocurrencies, has dropped 33% since it rebranded as Alpha Compute Corp. in April.

We talked about another one, Empery DIgital Inc., a few weeks ago. Maybe they'll all be really good at AI!

Trust.
If you are an executive at a big company, and you go to an off-site retreat with other executives, and at this retreat they do a "Vulnerability-Trust exercise" where they ask you to confess your deepest secrets to build trust with your colleagues, what you do is, you give a long sigh, you stare into space, a tear comes into your eyes, your lips quiver, and you say "you know, I've never told anyone this, but I feel like I can trust you all, so I'm just going to say it: Sometimes I'm too much of a perfectionist." And then you break down in tears and there's a group hug.

That is not actually career advice but if you tell people you've used drugs they are absolutely going to use it against you; what are you thinking? The New York Post reports:

A Netflix executive was fired from his $1.1 million a year job after revealing during a "trust exercise" at a work retreat that he had taken medically prescribed ketamine, a lawsuit has claimed.

Kevin Baillie, who was vice president and head of creative at Eyeline Studios, is suing the company after it launched an investigation into his comments that ultimately ended in his firing, the papers say. ...

During what's called a "Vulnerability-Trust exercise" at a January 2026 retreat at the exclusive Sendero Ranch, a Northern California property owned by Netflix, Baillie shared with his colleagues that he had undergone the treatment, the suit says.

I made myself laugh by imagining this happening at Bridgewater. But, honestly, anywhere, what are you doing trusting your colleagues and bosses?

Things happen.
Space X Falls 20% Below IPO Price, Erasing $1.2 Trillion Value. Hedge funds reap big profits from Wall Street index shake-ups. Chip Rout Deepens on Circular Funding, China Competition Fears. Pimco Embraces AI Boom on Its Own Terms. Deep Seek Founder's Hedge Funds Are Among Big Winners of CXMT IPO.Colombia Auditor Probes Bond Swap Over Constitutional Concerns. The Father of the 401(k) Has a New Savings Plan. Johnson & Johnson Agrees to Pay $5.5 Billion to Settle Talc Lawsuits. Dog Shelter Sues to Block Data Center That Risks Endangering 44,000 Animals. "Sysco buys technology from Cisco, while Sysco trucks deliver food to Cisco offices." Squirrel invades Tigers-Orioles game , and grounds crew is powerless to stop it.

=====================

Suspicious puts.
The basic job of a market maker is to buy low, sell high, and not get adversely selected. You bop along, buying stock at the bid (say, $9.99), selling it at the offer (say, $10.01), and collecting the bid/ask spread ($0.02) on each pair of trades. If people trade with you essentially at random, you keep the whole spread as profit: Someone comes in to sell at $9.99 one second, someone else comes in to buy at $10.01 the next, and you keep the $0.02.

But if someone sells you a lot of stock at $9.99 one second, and the next second the stock crashes to $9, then you lose $0.99. That is, roughly, adverse selection : buying stock when it is about to go down, or selling it when it's about to go up. What you want is to trade with people at random; what you don't want is to trade with people who know which way the price will move.

And so some of the business of market making is about avoiding adverse selection by knowing what the price should be, some of it is about charging a big enough spread to cover your adverse selection losses, and some of it is abouttrying not to trade with people who know too much . In the US stock market, one way to do that is to pay retail brokers totrade with their customers' orders : Retail traders, stereotypically, trade at random, so if you buy some stock from them you have no reason to think the stock will go down. 

That's stock market making. Options market making is the same but more so. Options tend to have wide bid/ask spreads and be bought by retail gamblers , so market makers make a lot of money on options. ( Robinhood makes about four times as much money from payment for order flow on options as it does on stocks.)

But options also have a lot of risk of adverse selection. If you sell someone short-dated out-of-the-money call options, they will probably expire worthless and you'll make money , unless the company announces a merger tomorrow. Then you'll get blown up. But the people buying those options might know something about a merger. There's a reason that my Second Law of Insider Trading is about buying short-dated out-of-the-money call options on merger targets. It happens a lot!

That is arguably an annoying example. Most of the time, "adverse selection" doesn't mean "insider trading." The more standard sort of adverse selection in market making is, like, a big institutional investor wants to buy a lot of shares, so the price goes up a lot after you sell some. Or a sophisticated hedge fund has done research suggesting that the fundamental value of a stock is above its price. Or a high-frequency trading firm knows that futures have moved up a tick before you do. Stuff like that.

On the other hand, if you are making markets in options to retail customers, adverse selection maybe ... does ... kind of mean insider trading? The customers are not managing billions of dollars or doing deep sophisticated research or running low-latency feeds from Chicago. If they buy a ton of options and then a merger is announced, that's probably either luck or insider trading, and if they do it multiple times , or if many of them do it , then perhaps you can rule out luck.

Perhaps this is unfair. Perhaps it is based on broad rude stereotypes about retail investors. But it is a nice theory for the market maker. Like:

You buy and sell options at big markups. Most of the time, the customers are putting in random noise bets and lose money. Sometimes, the customers have big wins. But those don't count: When the customers have big wins, it's because the customers cheated. So you shouldn't have to pay them. If you ran a literal casino, this logic would be very convenient. They'd win their bet, you'd say "nah you cheated," and you'd hold onto their money. Perhaps they'd sue you, or file a complaint with the regulators, and you'd eventually have to give them the money. But perhaps they wouldn't. For one thing, they probablydid cheat, so they'd lose in court or with the regulator. And even if they didn't cheat, what are they gonna say? "No I just did fundamental research and had a hunch that something good would happen to that stock"? Sounds fake.

If you are an options market maker, it's not quite as easy as that. The options trade on an exchange, the customers aren'tyour customers exactly, you don't control their accounts and you can't just keep the money.But here's this :

Susquehanna Investment Group is attempting to unmask the identities of individuals it claims made at least $100 million trading on inside information about a Chinese government crackdown on cross-border brokerages last month.

The Pennsylvania-based market-making firm, which says it was the counterparty on most of the alleged insider trades, sued 100 John Doe defendants in Manhattan federal court on Monday. Susquehanna is seeking to recover more than $70 million it says it lost to what it believes is one of the largest insider-trading schemes in recent memory.

According to Susquehanna, many of those trades were made from accounts at Interactive Brokers Group Inc., as well as the platforms of two firms targeted in the Chinese crackdown, Futu Holdings Ltd. and Up Fintech Holdings Ltd.'s Tiger Brokers. Susquehanna is seeking an order freezing certain accounts at those brokerages and authorizing subpoenas of them.

Here is Susquehanna's complaint , and here's its motion to freeze the brokerage accounts . Of course Susquehanna has nodirect evidence of insider trading. It doesn't even know who these traders were, so it certainly doesn't know whether they were insiders at Chinese regulatory agencies or brokerages, or what information they had when they made their trades. But it knows that they bought short-dated out-of-the-money put options on Futu and Tiger just before China announced the crackdown, that Futu's and Tiger's stocks crashed after the crackdown and that the puts made a lot of money, at Susquehanna's expense. "Insider Trading is the Only Plausible Explanation for the Subject Trades," says the complaint:

Upon information and belief, there was no new public information about FUTU or TIGR released between May 7, 2026 and May 21, 2026 that would provide a reasonable basis for the Defendants to place so many high-risk, high-reward purchases of short-dated FUTU and TIGR put options. ... Yet despite the lack of negative public news about Futu or UP between May 7 and 21, the Defendants placed unprecedented numbers of trades that would only be profitable if new information emerged in a matter of days or weeks that had a significant negative price impact on the stocks. Indeed, many of the options were purchased the day before the Crackdown News on May 22. Those facts provide powerful evidence of trading based on MNPI [material nonpublic information]. ...

The Defendants' identities are unknown to Plaintiffs. However, under any plausible scenario, the Defendants placed the Subject Trades in breach of a fiduciary duty or a duty of trust and confidence. The facts of this case suggest two categories of insiders who could have either traded directly on the MNPI about the imminent Crackdown News or tipped others: (i) Chinese securities regulators; and (ii) Futu and UP personnel who had knowledge of discussions with Chinese securities regulators about the enforcement action.

And so it is (1) suing the people who made the trades, (2) asking the brokers , Futu and Tiger and also Interactive Brokers , to reveal the identities of the people who made the trades, so it can sue them more effectively and (3) asking the court to order Futu and Tiger and Interactive Brokers to freeze their accounts so that, if it wins, it can get the money back. The only possible explanation for these trades is cheating, says Susquehanna, so it shouldn't have to pay.

Hedge fund AI.
"There are few ways in which a man can be more innocently employed than in getting money,"Samuel Johnson said , and I think about that a lot. Here is my extremely biased stylized history of the vibes in tech and finance over the last few decades:

After 2008, finance was Evil, and smart quantitative people wanted to be in tech, which was Good. "Don't Be Evil" was literally Google's mission statement, back when Goldman Sachs's was "relentlessly jamming our blood funnel into anything that smells like money." Tech was about Changing the World and Building the Future; finance was just about seeking money in exploitative ways. In the subsequent decade or so, people became a bit disillusioned with Big Tech. Googledeprecated "Don't Be Evil" in 2018. A certain cynicism set in about the problems tech was solving; Facebook's vision of the future was serving upever more addictive phone content to maximize advertising revenue. Political and cultural and social things happened that I won't get into. A smart quantitative person in 2020 might have thought "well, if I go to Google I will be building systems to maximize advertising revenue, and if I go to Hudson River Trading I will be building systems to maximize trading revenue, and those things are roughly morally equivalent but HRT doesn't go around moralizing about it, so I'll go to HRT." Finance hadn't become Good, but it was back to being Neutral; tech had gone from Good down to Possibly A Bit Evil. Then, in about November 2022 , the modern artificial intelligence boom started and everyone wanted to work at AI labs, in part because they could make incredible fortunes overnight but also in part because of a real sense of mission. Building artificial general intelligence might be the most important thing humanity ever does, so a smart quantitative person would rather work on that than on extracting short-term trading signals for stock options. Like 20 minutes later everyone started worrying that AI labs, instead of being Good, might actually be Incredibly Incredibly Evil. Like, if you go to work at Anthropic or Open AI or Google or x AI, you probably get good free snacks, but are you possibly working toward human extinction? Seems bad. If you are a cutting-edge AI researcher, you can also be very useful to a quantitative trading firm or hedge fund, and those guys probably aren't going to wipe out humanity. They just want to make money. Have the vibes swung back to finance? I wouldn't go that far, in part because there's just soooooooooo much money in AI labs, and the people who like finance mostly like money more. Still, Bloomberg's Nishant Kumar and Liza Tetley report :

Millennium Management is setting up an artificial intelligence laboratory to expand the development and application of cutting-edge technologies at the firm.

The new lab will become operational over the next few weeks, according to a memo seen by Bloomberg News. It will focus on accelerating early access and assessment of AI products, the memo said, as well as collaborating with AI firms on projects and attracting top AI talent.

The facility would "provide a highly entrepreneurial environment to attract and retain AI talent," Vlad Torgovnik, Millennium's chief information officer, said in the memo.

Right, hedge funds do need to be at the forefront of AI. Obviously their AI ambitions are a lot more modest than those of the big frontier AI labs. But maybe that's good.

Elliott cubs.
The general story about modern hedge funds is that bigger is better . Modern multistrategy multimanager hedge funds identify a mysterious quality known as investing skill, they hire people who have that skill, and they apply those people's skill to the largest possible opportunity set. Those people are expensive, and the funds hire lots of them to make lots of uncorrelated bets and maximize their risk-adjusted returns.

This is not the only way to run a hedge fund. The Financial Times has a story about alumni of Elliott Management , which is also a giant ($80 billion under management) hedge fund, also runs a bunch of strategies and is also hyper-focused on risk management. But its approach is a bit different from the classic multimanager pod shops:

Elliott now does everything from boardroom fights to takeover battles and distressed-debt brawls, all while maintaining an unusually intense eye on levels of risk.

The phrase Singer has used over the years to describe this phenomenon is "manual effort", or the attempt to eke out better returns by sheer force of will and resources. Elliott will often have as many as 50 employees devoted to one investment. ...

Elliott's hallmark investments also often combine legal expertise, a savvy for credit documents and sometimes entire takeovers, as eventually took place with the $16.5bn deal for software company Citrix in 2022.

Another former employee said working at Elliott was particularly good for learning "how to prosecute a wide variety of weird and hairy and messy situations".

The classic pod-shop trades are, like, "buy the stocks that will go up next week and short the ones that will go down," or "buy the stocks that will be added to the index," or "buy Treasury bonds and sell futures." Those trades tend to earn modest returns and benefit from a lot of leverage, and they naturally demand a large scale.

The classic Elliott trades are, like, "notice a mistake in a bond indenture ," or "ruthlessly make fun of a public-company CEO's drinking problem ." Those trades sort of have whatever scale they have: If there are $500 million of bonds with the mistaken indenture, or if the company with the drunk CEO has a $1 billion market capitalization, then that's the cap on the opportunity. It seems plausible that drunk CEOs and mistaken bond documents are more common at smaller targets. If you run an $80 billion fund, those trades might not move the needle. It's not worth it to apply a lot of manual effort to every drunk CEO or mistaken bond indenture.

On the other hand, if you're an Elliott employee, you can go start your own hedge fund and do Elliott-style trades that are too small for Elliott. The FT reports:

Members of the "Elliott diaspora", as some former staffers call it, have founded at least seven hedge funds since 2020, mirroring the "Tiger cubs" that came out of Julian Robertson's Tiger Management around the turn of the millennium.

They include Adam Katz's Irenic Capital, Dan Gropper's Carronade Capital, Quentin Koffey's Politan Capital and James Smith's Palliser Capital. Unlike Robertson, Elliott has not invested in any of the new firms, according to two people familiar with the matter. But even so, they have begun to find success in the same field.

Scale is a necessity for a pod shop, but it is perhaps an impediment for a weird-and-hairy-situation shop.

Stretch.
My basic model of Strategy and its "Stretch" preferred stock is that it is soft fuzzy banking. That is:

Strategy issues Stretch to raise money to buy Bitcoin. Stretch is a perpetual preferred stock whose dividend rate resets each month to make it trade at par. It's effectively short-term financing that automatically rolls over each month at whatever Strategy's market-clearing interest rate is. Stretch is intended to work like a money-market instrument, like a bank deposit or money market fund . One dollar of Stretch is supposed to always be worth a dollar; it is supposed to have very little duration. Strategy is essentially funding Bitcoin purchases with bank deposits. But not really. Unlike bank depositors, Stretch holders can't take their money out whenever they want; Stretch is perpetual. The monthly interest-rate reset isalmost economically equivalent to refinancing Stretch every month, but not quite; if financing markets are shut the Stretch stays outstanding. Also, Strategy doesn't actuallyhave to pay that interest: It's a preferred stock, and Strategy can always decline to pay the dividend. And while Strategy announced an intent to reset the dividend every month to make Stretch trade at par, you can't hold it to that intent: It could just reset the dividend to a lower rate and allow Stretch to trade below par. Soft fuzzy banking is in some ways superior to regular banking: If actual banks could fund themselves like this, it would be nice. On the other hand:

Actual banks can't. This only works if you're a goofy Bitcoin treasury company with an audience of true believers. Also the tradeoff is that the cost of this capital is quite high. As of last week, Stretch was (1) paying 11.5% and (2) trading around 75 cents on the dollar, implying that its yield (to trade at par) should be about 15%. So, more than a bank deposit. As we discussed last week , Strategy is now facing something of a soft fuzzy bank run: As Stretch has traded down, Strategy's cost of capital has gone up, and it has faced pressure to (1) pay much higher interest on the Stretch and/or (2) payback the Stretch , just like a regular bank facing a regular bank run. Less so, because all of this is technically optional, but still sort of a bank run. And yesterday Strategyannounced that :

It no longer intends to keep Stretch at par: It's raising the interest rate to 12%, not 15%, and in the future "will not necessarily increase the STRC dividend rate solely because STRC trades below its stated amount." (It closed yesterday at 83.67 cents on the dollar.) It is buying back some Stretch: It's spending up to $1 billion to buy back its preferred stock, and "currently expects STRC to be the initial priority under the program." It's doing assorted other stuff , building its cash reserve, selling some Bitcoin , to improve its liquidity so that it can keep paying all those Stretch dividends. Some of it , paying higher interest, redeeming deposits, selling assets , shoring up its balance sheet , is what banks would do in a bank run. Some of it , not payingmuch higher interest, only redeeming some of the deposits (and not at par) , is what banks would prefer to do in a bank run, if they could, but they can't.

Space X misunderstanding.
A few weeks ago, ahead of Space X's initial public offering, we talked about the technical complexities of keeping track of orders in a giant IPO. Those complexities are ... not very complex? It's, like, you call your clients and ask them how many shares they want, and then you write their answers down on a list. But we discussed a Bloomberg News story about how various banks and brokers and depositories and service providers were doing various sorts of practice runs to make sure they were ready for the Space X IPO, because that IPO would be a severe test of their systems (for writing down lists). I wrote:

On the one hand all of this is true, but on the other hand if Space X's banks come to it tomorrow and say "ahhh there were just too many orders for stock so we lost track of them" that will not be acceptable.

Well! Well. "Space X IPO Left Korea Broker With No Shares on Misunderstanding," Bloomberg reports :

Mirae Asset Securities Co. ... inadvertently treated an early request to indicate investor interest as the point at which it had submitted binding orders, the people said. As a result, more than $1.1 billion worth of Korean demand was never entered into the IPO order book, they said. ...

In mid-May, weeks before bookbuilding began, the bookrunners circulated an email asking underwriters to indicate investor demand, which was aggregated in a virtual data room in line with standard practice for large deals.

Mirae responded to that request believing it had placed its clients' orders, according to some of the people, who are familiar with the firm's thinking. But from the perspective of the Wall Street banks running the deal, those responses were only indications of interest, not bids. The actual orders were entered in June after a separate email from the bookrunners, as is customary for such IPOs.

Right, again, the technical complexities here are just not that complex: They ask you before the IPO "hey how many shares do you think you can sell," and then they ask you at the end of the IPO "please submit the orders for the shares you sold," and then they use thesecond list to allocate you some shares. And if you forget, oops, no shares. "The New York-based banks viewed Mirae as having submitted zero retail orders, and ultimately allocated it zero retail shares." And:

"We bow our heads in apology for delivering such unfortunate and heavy news to customers who participated in the Space X IPO subscription with great interest and anticipation," Mirae Vice Chairmen Kim Mi-seop and Heo Seon-ho said in a text message to clients on June 15, the Seoul Economic Daily reported. They pledged a review of the process and measures to "restore consumer trust," according to the newspaper.

Yeah there is no good way to tell clients "sorry, we lost track of your stock orders."

Things happen.
How the great wealth transfer is rattling Wall Street. Space X Pushes US Share Sales to Record $251 Billion at Midyear. Private equity fund investors turn todebt-like deals in downturn. World Bank drops climate finance target under US pressure. Ethiopia Bondholders Criticise IMF for 'Poorly' Handled Debt Rework. EY employee charged with accessing Australian prime minister'sbank details . QSBS trust stacking . New York's Pied--Terre Tax Stymies Owners Looking for Loopholes. People arebetting on wildfires . Partly this is a matter of snobbery about retail investors' information and analysis, but a lot of it is about size: If a giant index fund sells you some stock, the stock will probably go down, just because the index fund will probably keep selling more stock. If a retail investors sells you some stock, that's probably all the stock she has. There is another important element of options market making, which is that options market makers classicallydelta hedge in the underlying stock: If you buy a call option on 100 shares of stock, you might sell 60 shares to remain delta-neutral, and then sell more shares or buy them back as the stock goes up or down. Options market makers don't *haveto do this, and might lean in one direction or another, or hedge their options exposure with other options rather than with stock delta. But in general even a delta-hedged market maker is still exposed to gap risk: If you're short a put and short your Black-Scholes delta of the underlying stock, and the stock craters, the delta gaps up and you are not short *enoughstock to be fully hedged. Also, retail options internalization is a bit different mechanically from retail stock internalization , every trade has to print on an exchange , in ways that don't especially concern us here. That's a joke. That was not actually Goldman's mission statement. Also, disclosure, I worked at Goldman at the time, and part of the bias in this stylized history comes from the fact that I personally looked around in 2008 and thought "well *I'mnot very evil, am I?" I don't have great evidence for that. There's a Robert Harris novel about, what if they did? I originally wanted to link to the Windstream situation , but that was led by Aurelius, not Elliott. Though Aurelius is alsoan Elliott cub .

 

Before it's here, it's on the Bloomberg Terminal. Find out more about how the Terminal delivers information and analysis that financial professionals can't find anywhere else.Learn more .

=====================

22.8333/5 trading.
Most stock exchanges run on old-fashioned human timeframes. The exchange opens after breakfast, it trades for a while, and then it closes so people can wrap things up, organize all of their trade tickets and leave in time for dinner. Several important exchanges close for lunch. These exchanges all started as trading floors where humans met to trade stocks and wrote down trade tickets by hand; those humans needed time to get organized and also to eat.

Obviously now the way you expect to trade stock , or crypto or sports bets or anything else , is by clicking a button on your computer or phone. Your computer does not need a lunch break. You might intuitively assume that it doesn't need a pause to organize the day's trading and reconcile its records, either. "What, when I sell you stock, the computer takes the stock out of my account and puts it in your account; that should happen instantly; it doesn't need to later go back and update all the records to reflect the day's trading." There is a sense that the old human rhythm of the trading day is outdated, and a push , caused in part by the rise of active retail trading, in part by globalization of stock markets and in part by the example of crypto , for continuous 24-hour-a-day, seven-day-a-week electronic trading.

Still that seems sort of mean. The computers can do a lot of the trading, but humans still need to supervise them; those humans can eat at their desks, I guess, but they need to sleep eventually. Also it turns out that the systems for keeping track of stuff , for financing and reconciling and settling trades , are a bit more complicated than you'd intuitively assume, and the computers would appreciate a little pause to get their books in order.

Anyway here's a press release from the London Stock Exchange:

London Stock Exchange today announced plans to launch London Stock Exchange 24 (LSE 24), a new 24/5 trading venue to support the next generation of digital, algorithmic and agentic trading.

LSE 24 is designed to support near-continuous trading from Monday to Friday, giving global investors greater flexibility to respond to market events, access liquidity across time zones and manage risk. The venue, which will be built on LSEG's trusted financial market infrastructure, complements existing market structures by preserving the resilience and integrity of regular trading hours, while opening new opportunities for participation outside the traditional trading day in the UK. It will operate separately from the London Stock Exchange's Main Market, which will continue to operate its existing trading hours.

A delightful footnote clarifies:

London Stock Exchange 24 will operate from 17:00 to 07:50 with a 30-minute pause between 18:30 and 19:00 to apply End of Day processes. Trading will continue on the London Stock Exchange's Main Market between 08:00 and 16:30.

This is conventionally called "24/5" , trading 24 hours a day, 5 days a week , but of course it isn't. The main market is open 8 a.m. to 4:30 p.m. (8.5 hours). The after-hours market is open 5 p.m. to 7:50 a.m. (14 hours and 50 minutes) , with a half-hour break after the main session ends and a ten-minute break before the next one starts , except it's paused between 6:30 and 7 p.m. "to apply End of Day processes," so it actually trades for 14 hours and 20 minutes. So a total of 22 hours and 50 minutes of trading per day.

All of this stuff also involves blockchain , "LSE 24 will leverage LSEG's Digital Securities Depository (DSD), ... [which] creates the foundation for the digitisation of issuance, settlement and asset servicing," etc. , and I suppose the appeal of putting stocks on the blockchain is that eventually it will allow for instantaneous settlement, cut out all the end of day reconciliation processes, and allow, you know, 23/5 or 24/5 or even 24/7 trading. The blockchain doesn't need lunch.

Intra-Black Rock LME.
The old-fashioned theory is that, if your company has borrowed a lot of money and can't pay it back, you will have a hard time borrowing more money. Potential lenders will be like "why don't you pay back the new loans before we lend you more money?" Your existing loans will probably have covenants limiting your ability to borrow more money. Those loans might also be secured by liens on all of your assets, so the lenders get first dibs on the assets if you can't pay them back. This is all pretty intuitive and how it is supposed to work: The people who loaned you the money expected to get paid back, and they wrote contracts to make it more likely that they'd get paid back.

The edgy postmodern theory is that you can pretty much always find some way around those contracts: There is always some maneuver you can use to move some assets out of reach of your creditors so you can borrow more money, some narrow path through the covenants that will allow you to borrow more money at the expense of your existing creditors. This is honestly kind of weird, and I am exaggerating a bit , not always , but it does come up a lot. These maneuvers , borrowing new money in ways that extract value from existing lenders , are generally called "liability management exercises," or LMEs, and they are why debt lawyers can make like $30 million a year now.

This is, broadly speaking, (1) good for the lenders providing you new money (they get reasonably good collateral and a high interest rate) but (2) bad for creditors overall: It is better for lenders in expectation if loans are predictably paid back, and worse if there is always some trick available to take collateral from existing creditors and give it to new ones.

One way that lenders push back on this is by sticking together: If you run into financial trouble, all of your lenders could get together and agree not to cut any side deals with you that will advantage some of them at the expense of others. This is called a "cooperation agreement," and we have talked about it a few times because it arguably raises antitrust problems (but probably not). If all of your lenders agree not to cut side deals, that limits your ability to do LMEs with them. Of course, you could try to do LMEs with new lenders who aren't part of the cooperation agreement, but:

It's often easier to do LMEs with existing lenders (who can vote to amend your existing loans) than new ones; and.
If you are a big company, you might have borrowed from most of the potential lenders anyway, so there might be no one left outside the cooperation agreement to give you a new loan.

The first problem is sometimes avoidable; sometimes you can take value from existing lenders and give it to new ones without any vote of existing lenders. As for the second problem ... look, credit investing firms are tough places. If you have borrowed from one portfolio manager at a firm, and if that portfolio manager has signed a cooperation agreement and promised not to lend you any more money, what if you called a different portfolio manager at the same firm? "Hey, do you want to make some good money at the expense of your colleague down the hall? What if we took some of her collateral and gave it to you?" That's a good pitch! The competition with the person down the hall is fiercer and more ever-present than competition with other firms.

I'm kidding, mostly, but not entirely. Here's a fun story from Bloomberg's Irene Garcia Perez, Giulia Morpurgo and Kat Hidalgo:

A potential debt revamp at Aston Martin Lagonda Global Holdings Plc is setting the stage for an unusual battle between different divisions of asset management giant Black Rock Inc.

On one side are the firm's bond funds, some of which fall under the purview of global fixed income chief investment officer Rick Rieder. They're part of the creditor steering committee that organized to present a united front to the company if it needed to discuss a refinancing of its debt or raise additional liquidity.

Meanwhile, Aston Martin has been in talks with money managers including HPS Investment Partners , the private credit specialist founded by Scott Kapnick, Scot French and Michael Patterson that Black Rock acquired a year ago , to raise fresh cash by moving assets out of the reach of existing lenders. ...

Black Rock's exposure to Aston Martin's bonds sits in both actively managed and index-tracking funds, Bloomberg data show. The firm joined other creditors, including more opportunistic buyers, in signing a cooperation pact binding them to act in concert in debt talks with the company, out of fears Aston Martin was looking to raise financing elsewhere. ...

The maneuver being discussed, known as a drop-down, could be costly for existing creditors. In a sign of the impact, Aston Martin bonds plunged by as much as 10 cents on the dollar last week, to their lowest level on record, after Bloomberg reported on the debt talks.

HPS is part of Black Rock, but it's not Black Rock Black Rock; it has some independent existence. And it can make its own investment decisions:

Black Rock, in its own SEC disclosure, said there might be potential conflicts of interest between its funds. It said it would mitigate those with "independent investment decisions," with each account acting in its own best interest.

Seems fine! Black Rock runs different funds, and the managers of those funds have fiduciary duties to the investors in those funds. The managers of the funds that hold Aston Martin bonds have a duty to try to get paid back, but the managers of the HPS funds arguably have a duty to try to stiff the bondholders and collect more money for themselves. This all feels a bit second-best, but it is the world we live in.

Paramount/Warner.
In February, Paramount Skydance Corp. agreed to buy Warner Bros. Discovery Inc. for an equity value of about $81 billion and a total value of $110 billion including debt. When that deal closes, Paramount will have to pay something like $81 billion for Warner's stock, plus $15 billion to repay some of Warner's existing debt. It will finance those payments with a combination of (1) $47 billion of new stock and (2) $49 billion of new debt. Footnote: See pages 96-97 of the merger proxy, and the "Transaction Highlights" of the press release, for a breakdown. The new debt will come in various tranches that will pay interest of, say, 6% to 8% per year. Footnote: Bloomberg News reported a few weeks ago: "The investment-grade loans are being discussed at spreads of about 250 to 275 basis points over the benchmark rate, some of the people said. Pricing on the senior secured bonds will vary by maturity, with five-, eight- and 10-year tranches under consideration. The euro-denominated second-lien bonds may yield about 7.25% to 7.5% on the eight-year notes and 6% to 7% on the five-year notes. Comparable dollar-denominated notes are expected to yield about 1 percentage point more, the people said." The SOFR benchmark rate is about 3.57%, implying a loan pricing of around 6% or so. The holders of the new stock , largely Larry Ellison and Red Bird Capital , will own the equity upside of Warner. (Also of Paramount, but Paramount is smaller than Warner, so the combined company will be mostly Warner.)

That's when the deal closes. Paramount has not yet issued that debt or stock, because it doesn't need the money yet, because it doesn't have to pay for Warner yet. Companies do not regularly go around pre-funding $100 billion acquisitions. "Banks have lined up enough investor demand to cover most of the roughly $49 billion debt package backing Paramount Skydance Corp.'s takeover of Warner Bros. Discovery Inc. , long before launching the deal," Bloomberg's Claire Ruckin reported a few weeks ago, and the money will be there, but it's not there yet.

Until closing, Warner will be financed by (1) its existing debt, which pays interest of between 3.9% and 7.7% and (2) $81 billion of stock, held by its existing stockholders. Warner's stockholders do not expect any equity upside. They've already agreed to be cashed out for $81 billion ($31 per share). They do, however, get some interest, eventually: "In the event the transaction has not closed by September 30, 2026, WBD shareholders will receive a $0.25 per share 'ticking fee' for each quarter (measured daily) until closing." That's $1 per share per year, or about 3.2%.

In some sense that is ... cheap financing? Like, at this point, Paramount's shareholders probably own the equity upside of Warner: When the deal eventually closes, Warner will belong to Paramount. But until then, they are mostly financing Warner with an $81 billion IOU that pays zero interest now, and will start paying 3.2% interest in September. I suppose you could look at it differently. Bloomberg's Josh Sisco and Hannah Miller report:

Paramount Skydance Corp. was on the brink of closing its blockbuster $110 billion takeover of Warner Bros. Discovery Inc. Now the companies are facing a legal hurdle that risks putting the deal on hold for months at a cost that could quickly climb to billions of dollars.

On Monday, a federal judge granted a request from states challenging the deal to pause the tie-up for two weeks, saying it "likely" violates antitrust law. But that could be just the start of a much longer delay. In early August, US District Judge Araceli Martnez-Olgun will hold a hearing in Oakland, California, to determine whether the acquisition should be put on ice pending the outcome of a full trial. ...

Now, Paramount is facing a race against the clock. If it doesn't close the deal by the end of September, Paramount must pay late fees to Warner Bros.' shareholders of about $7 million per day. That makes an April trial date an eternity for the company that was so close to tying the knot. With the daily fee, an April trial could total well over $1 billion in extra costs to Paramount.

Sure but if they close the interest rate goes up? Obviously the downside for Paramount is mostly that it can't start running Warner yet; it doesn't really own Warner until the deal closes. And the deal might never close; then it is paying 3.2% interest to finance an asset it will never own.

Polymarket.
Bloomberg's Denitsa Tsekova and Leonardo Nicoletti have an article today about possible insider trading on Polymarket. I write a lot about insider trading on mergers, and the tells are pretty well-known at this point. If you have never traded stock options before in your life, but one day you buy a lot of cheap short-dated out-of-the-money call options on some company, and the next day it announces that it's being acquired and the stock shoots up, that's probably insider trading. You might look for the same basic tells , never doing it before, doing one cheap low-probability trade, having it pay off quickly , in prediction markets, and Polymarket trades are public. So:

A Bloomberg Businessweek analysis found that trades with characteristics often associated with insider activity became more prevalent on Polymarket starting this January. The review focused on 34,000 transactions flagged as potential insider trades by analytics platform Polysights from August 2025 to June 2026. They were made on Polymarket's global exchange, where, unlike on Kalshi, they're publicly visible on a blockchain network. (Polymarket also operates a US-based exchange that's limited mainly to sports.)

Polysights flags a trade after scoring it across eight different metrics, including how much money the trader is wagering, how recently their account was created, how low the odds were when the trader entered the market and how much of the trader's volume is concentrated across one or a few markets. This approach can identify transactions that differ markedly from typical market behavior, but it can't answer definitively whether a trader skirted rules governing the use of nonpublic information. Some bettors could simply have had better research, insight or luck.

Fine, sure, maybe some insider trading. Here's the thing about Polymarket that drives me nuts, though. As I keep saying, it is very weird that:

Polymarket is not allowed to take any bets from US traders on its main exchange, insider trading or not; but.
It does, all of the time; that appears to be much of its business, it advertises constantly in the US, and nobody seems to care. The US Commodity Futures Trading Commission last year "killed [an] investigation into whether Polymarket was illegally serving U.S. customers," even though absolutely everyone knows that it is.

Tsekova and Nicoletti write:

For American regulators, the question of whether trades originate in the US is crucial. It can also be difficult to answer. In Polymarket's case, the cryptocurrency transfers customers use to fund trades and cash out rely on digital wallets whose addresses are visible but aren't attached to a physical location. And while exchanges created and regulated in the US, like Coinbase, are mostly used by traders situated there, foreign users can access them too. (Coinbase says that it takes steps to ensure all activity on its exchange is lawful and that it works routinely with law enforcement.)

What is clear is that a large volume of trading on Polymarket's global platform is going through US-regulated exchanges. Roughly half of all traceable trading volume since January 2021 , about $21 billion , came from wallets funded by such exchanges, according to exclusive data from blockchain intelligence firm Dune. For US geopolitical and political markets tied to Iran, the share was far higher, reaching 70%. And flagged trades related to these markets since last July were almost three times more likely to have been funded through US-regulated crypto exchanges than through other sources.

It is not certain that every trade on US politics funded from a US-based crypto exchange is done by a US-based trader, but it is certain that some of them are (because those people keep getting arrested for insider trading!). As a legal matter, Polymarket is not allowed to take bets from US customers on its main exchange; as a practical matter, it is.

Are these facts related? I mean:

If your Polymarket trading is illegal anyway, might you also be tempted to illegally insider trade on Polymarket?

If there's never any enforcement against Polymarket for letting US customers trade illegally, might you assume that there won't be much enforcement against customers who insider trade?

Might you assume that, if Polymarket is letting you trade illegally, it will be hesitant to report any insider trading, since it's technically not allowed to take your bets anyway?

That's not any sort of advice, don't insider trade, but the general regulatory stance of giggling at illegal Polymarket trading probably does encourage some illegal Polymarket insider trading.

Elsewhere: wedding prop bets.

Gold?

Here is a very fun article in the Daily Progress noting that Dominion Energy's proposed Valley Link transmission line project in Virginia , "sold to the state as a necessary undertaking to feed Virginia's power-hungry data centers" , happens to run through a historical gold mining belt, and just hinting that that might have been intentional:

"Unfortunately the information on mines is not available at this time," Dominion spokesman Craig Carper said in an email. "We're still in the conceptual phase of the project. We'll know more as the project develops." ...

While many are questioning why the project can't be built elsewhere, fewer have inquired why Dominion chose this particular route in the first place.

During an interview back in March, Dominion representatives said The Daily Progress was the first news outlet to inquire about the land underneath the project as opposed to the path laid before it.

None of the three representatives present could go into much detail, but Carper, who was there, confirmed, "We will disrupt a portion of properties during construction."

While not guaranteed, that disruption could easily turn up gold, industry experts say. And because Dominion will own the land, it will own the gold underneath it. In fact, before Dominion will be able to erect its transmission towers, it will have to take soil samples and excavate earth , not unlike the early stages of gold prospecting and mining.

I have to say, if I was writing a conspiratorial financial thriller, I would write about a gold mine , an old-fashioned commodity business , that was used as cover for secretly building data centers. Data centers are the new gold, etc. But I concede that this thriller , data centers as a cover for secret gold mining , might be easier to pitch to Hollywood.

Things happen.
Wall Street Talks Up Carry Trade as Returns Soar Most in Decades. Black Rock Leads $12 Billion Financing for New Meta Data Centers in Texas. Bessent Says US to Scrutinize Chinese AI Models for IP Theft. China weighs tighter export controls on AI models and chips. Quant Hedge Fund 'Free Fall' Spooks Wealthy Investors in China. Deep Seek Founder's Fund Slumps 16% as AI Rout Hits China Quants. Carlyle in Talks to Hand ESG Consulting Firm Over to Bridgepoint. Kalshi Seeks Approval for Perpetual Futures Tied to Gold, Silver. The Man Who Runs the IRS Spied on Colleagues When He Worked at JPMorgan. Tom Hayes says UBS's 'Project Chocolate' probe shows he was targeted from outset. The New Jersey Financier Behind Trump Media's Pivot Into Nuclear Energy.

===== YOUR TASK =====
Write the opening for today's edition from the items you are given. Each item is a headline followed by its full primary text under "--- STORY ---" (a fetched article or a Hacker News self-post). The items are given in the editor's consequence order, most important first. Choose your one or two sections from the top items (roughly the top five), strongly preferring the most important; pass over a top item only if there is genuinely too little to say about it. Do NOT pick a lower-ranked item just because it happens to have more text. Give each chosen item its own short titled section. Because you have the full primary text: ground everything in it and invent nothing (no numbers, names or quotes not present); you MAY quote short verbatim phrases from the story text, the way the examples quote their sources, then react. Keep it tight, as the examples do: the news lands as a short beat and then you react, you do not recount the article at length. Rules: start each section with its short title alone on one line, prefixed with a single "# " (for example: # Emergent cyber capability), and use no other markdown anywhere; no em or en dashes; straight ASCII quotes and apostrophes only; no bullet lists; no references to prior editions; no math notation. Output prose only."""


def qualifying_body(row: dict) -> str | None:
    """Return the row's substantial primary body, or None if it doesn't qualify.

    A full fetched article (>= HN_TAKE_MIN_CHARS) qualifies; otherwise a self/text
    post (item_type in story/ask/show) whose text >= HN_TAKE_MIN_CHARS qualifies.
    Comments never qualify a story on their own.
    """
    article = (row.get("article_content") or "").strip()
    if len(article) >= HN_TAKE_MIN_CHARS:
        return article
    text = (row.get("text") or "").strip()
    item_type = (row.get("item_type") or "").strip().lower()
    if item_type in _SELF_POST_TYPES and len(text) >= HN_TAKE_MIN_CHARS:
        return text
    return None


def build_take_context(rows: list[dict]) -> str:
    """Assemble the prompt input from qualifying rows, biggest HN score first.

    Only each story's primary content is fed (article or self-post); the comment
    discussion is intentionally excluded. Returns '' when no story qualifies (the
    caller then produces no column).
    """
    eligible = []
    for row in rows:
        body = qualifying_body(row)
        if body is not None:
            eligible.append((row, body))
    eligible.sort(key=lambda rb: int(rb[0].get("score") or 0), reverse=True)
    eligible = eligible[:HN_TAKE_MAX_STORIES]

    blocks = []
    for row, body in eligible:
        header = (
            f"[{row.get('rank')}] {row.get('title')}  "
            f"(score {row.get('score', 0)}, {row.get('comment_count', 0)} comments)\n"
            f"url: {row.get('url') or ''}"
        )
        blocks.append("\n".join([header, "--- STORY ---", body[:HN_TAKE_BODY_CAP]]))
    return "\n\n".join(blocks)


def _client() -> "OpenAI | None":
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        logging.error("OPENAI_API_KEY not set; cannot generate the HN Take")
        return None
    return OpenAI(api_key=key, max_retries=6)


def generate_take(context: str) -> str | None:
    """Run the one OpenAI Responses call; return the column markdown or None.

    Fail-open: missing key, empty output, or any OpenAIError -> None (the error
    is recorded in API_ERRORS so the daily run can surface one notification).
    """
    try:
        client = _client()
        if client is None:
            return None
        out = client.responses.create(
            model=HN_TAKE_MODEL,
            instructions=HN_TAKE_SYSTEM,
            input=context,
            reasoning={"effort": HN_TAKE_REASONING},
            timeout=300,
            prompt_cache_options={"mode": "explicit"},
        ).output_text.strip()
    except OpenAIError as exc:
        logging.exception("HN Take generation request failed")
        API_ERRORS.append(f"{type(exc).__name__}: {exc}")
        return None
    return out or None


def load_cached_take(conn, run_day) -> str | None:
    """Return the cached column for (run_day, model, prompt_version), or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT take_md FROM hn_takes "
            "WHERE run_date = %s AND model = %s AND prompt_version = %s",
            (run_day, HN_TAKE_MODEL, HN_TAKE_PROMPT_VERSION),
        )
        row = cur.fetchone()
    return row[0] if row else None


def store_take(conn, run_day, take_md, context_input) -> None:
    """Upsert the generated column keyed on (run_date, model, prompt_version).

    Persists both the output (``take_md``) and the exact assembled prompt
    ``context_input`` that produced it, so a column can be audited or
    reproduced against its input.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hn_takes (run_date, model, prompt_version, take_md, context_input) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (run_date, model, prompt_version) "
            "DO UPDATE SET take_md = EXCLUDED.take_md, "
            "context_input = EXCLUDED.context_input, generated_at = NOW()",
            (run_day, HN_TAKE_MODEL, HN_TAKE_PROMPT_VERSION, take_md, context_input),
        )


def get_or_generate(conn, rows, run_day) -> str | None:
    """Return the day's column: cached if present, else assemble -> generate -> store.

    Returns None (no column) when nothing qualifies or generation fails.
    """
    cached = load_cached_take(conn, run_day)
    if cached is not None:
        return cached
    context = build_take_context(rows)
    if not context:
        return None
    take_md = generate_take(context)
    if not take_md:
        return None
    store_take(conn, run_day, take_md, context)
    return take_md


def enabled() -> bool:
    """True when the column is switched on and an OpenAI key is present."""
    flag = os.environ.get("HN_TAKE_ENABLED", "1").strip().lower()
    if flag in ("0", "false", "no", "off", ""):
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))
