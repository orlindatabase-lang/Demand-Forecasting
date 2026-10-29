/** "About" text for each Design Suggestions feature - descriptive, but in
 * simple everyday words with examples (2026-10-09 / 2026-10-10,
 * user-requested). Keep in step with api/design_suggest.py when the method
 * changes. */

export interface AboutSection {
  heading: string;
  points: string[];
}

export interface AboutContent {
  title: string;
  sections: AboutSection[];
}

export type AboutKey =
  | "lifecycle"
  | "trending"
  | "products"
  | "styles"
  | "drivers"
  | "trendStyles";

export const ABOUT: Record<AboutKey, AboutContent> = {
  trendStyles: {
    title: "Styles in this trend",
    sections: [
      {
        heading: "What it shows",
        points: [
          "The designs behind this trend and the pieces each sold: NOW = latest 4 report weeks (so far), BEFORE = the 4 weeks before.",
          "▲ green = selling more, ▼ red = selling less, NEW = did not sell before.",
        ],
      },
    ],
  },
  lifecycle: {
    title: "Trend status",
    sections: [
      {
        heading: "What it shows",
        points: [
          "It tells you which product types (sub categories), colours, fabrics and necks are becoming popular, which are selling steadily, which are slowing down, and which are no longer popular.",
          "Use the buttons at the top (Sub Category, Colour, Fabric, Neck) to switch between them.",
          "Every item is placed in one of four groups:",
          "▲ Trending — people are buying it much more than a month ago.",
          "● In trend — selling steadily, no big change.",
          "▼ Starting to fade — people are buying it much less than a month ago.",
          "⊘ Out of trend — it sells far less than it used to, or it has stopped selling.",
        ],
      },
      {
        heading: "Reading an item",
        points: [
          "\"5.2% of sales now\" — out of every 100 pieces we sold in the last month, about 5 were this item.",
          "\"41% below its best\" — it used to sell more. In its best month it was 8.7 out of every 100 pieces; now it is 5.2.",
          "The number on the right (for example +6% or −27%) — how much it went up or down compared with the month before.",
          "The small line — a mini chart of the last 6 months, one point per month. Left is 6 months ago, right is now. The coloured dot is the current month. Line going up = getting more popular; going down = losing popularity.",
          "Hover an item to see its exact number for each month.",
        ],
      },
      {
        heading: "⚡ Sudden spike",
        points: [
          "A ⚡ tag means the item suddenly sold much more in one week than usual — at least 1.5 times its normal part of sales.",
          "Example: \"⚡ ×2.2\" means it sold about twice as much as usual that week, compared with everything else.",
          "If a ⚡ appears on something that is fading or out of trend, it may be coming back. Watch it, and make a small quantity first.",
        ],
      },
      {
        heading: "Why we count \"out of every 100 pieces\" and not just pieces",
        points: [
          "During a festival or a sale, almost everything sells more. If we only counted pieces, everything would look like it is getting popular.",
          "Counting \"out of every 100 pieces\" shows what people really prefer, no matter how busy the month is.",
        ],
      },
      {
        heading: "Weeks and the current week",
        points: [
          "We use the same weeks as the Weekly Sales Report: W1 = 1st–7th, W2 = 8th–15th, W3 = 16th–23rd, W4 = 24th–end of the month. One \"month\" here = 4 of these weeks.",
          "The current week is not over yet. So for the days still to come we use that week's forecast (the same one shown in the Weekly Sales Report), raised or lowered to match how fast things are really selling this week. That way an unfinished week never looks like a drop.",
          "Orders from marketplaces (like Myntra or Ajio) sometimes reach us a few days late. We correct for that too, so late orders do not make an item look like it is fading.",
        ],
      },
      {
        heading: "How it is used",
        points: [
          "The Suggestion tab only suggests items that are ▲ Trending or ● In trend.",
          "Items that are fading or out of trend appear there only under \"Cut back\".",
        ],
      },
    ],
  },
  trending: {
    title: "Trending designs",
    sections: [
      {
        heading: "What it shows",
        points: [
          "The individual designs (design numbers) whose sales are growing the fastest right now.",
          "\"New launch\" means the design started selling in the last 8 weeks.",
          "This table is about single designs only. For colours, fabrics, necks and product types, see Trend status above.",
        ],
      },
      {
        heading: "Reading the table",
        points: [
          "Sold so far — real pieces sold in the last month (the last 4 report weeks).",
          "4 weeks before — real pieces sold in the month before that.",
          "Growth — how much more it is selling now than in the month before. Example: +77% means it sells about three-quarters more.",
          "Because the current week is not over, growth also counts the pieces we expect to sell in the days left this week. Hover a growth number to see the exact calculation.",
          "A design is shown only if it is selling at least 30% more. The biggest increase is at the top.",
        ],
      },
      {
        heading: "What to do",
        points: [
          "Make more of the designs at the top, or make new designs like them.",
          "Look at what the top designs have in common — the same colour, fabric or neck is a strong hint.",
          "A huge growth on a design that sells only a few pieces is an early sign, not a sure thing. Try a small quantity first.",
          "During a sale, many designs grow at the same time. Trend status shows what is really getting popular beyond the sale.",
        ],
      },
    ],
  },
  styles: {
    title: "Styles to push",
    sections: [
      {
        heading: "What it shows",
        points: [
          "Our existing designs that already have the features the product cards below suggest - so you can push them now instead of waiting for new designs.",
          "It is based mainly on sub-category and colour: a design is listed only when its sub-category has a card below AND its colour is one of the colours that card suggests.",
          "The order follows the cards: the strongest sub-category first, and inside it the card's colours in their order (best colour first).",
          "Inside one colour, designs with the most other suggested features (fabric, embroidery, neck, sleeve, top type) are on top; the same match goes to the one expected to sell more.",
          "Only designs expected to sell in the next 9 weeks are listed. A sub-category whose card suggests no colour is not listed.",
        ],
      },
      {
        heading: "Reading a row",
        points: [
          "Features — 8/8 means the design has every feature its card suggests. Green = matches. Grey and crossed out = does not match; it shows what the design has instead.",
          "Matching features are listed in trend order: ▲ Trending first, then ● In trend, then details that have no trend status (embroidery, sleeve, top type). The mark before the sub-category name is the sub-category's own trend.",
          "Columns: Colour, Sub-category, Features, Expected sale. Each top row is one colour of one sub-category (for example Red · Anarkali KPD Set), best sub-category and colour first.",
          "Features on a top row = the other features its card suggests (fabric, embroidery, neck, sleeve, top type). Expected sale on a top row = the total of its designs. Click the row to open its designs (the first row is open at the start; Expand all / Collapse all open or close every row). Click a design's photo to see it large.",
          "Week columns — laid out like the Weekly Sales Report: the next 9 report weeks (W1 1-7, W2 8-15, W3 16-23, W4 24-end) grouped by month, with a TOTAL per month. Top number = sold so far (running week only), orange number = forecast. A coloured week heading is a festival (orange), sale (purple) or both; hover it for the dates and event.",
          "Expected sale — this design's forecast for the next 9 weeks: the running report week plus the 8 after it (W1 1-7, W2 8-15, W3 16-23, W4 24-end), the same numbers as the Weekly Sales Report. The running week counts what has sold so far plus the current pace for the days left.",
          "Sub-category — how strong its sub-category's card is (Strong, Good or Try small).",
        ],
      },
    ],
  },
  products: {
    title: "Suggestion: one card per product",
    sections: [
      {
        heading: "What it shows",
        points: [
          "One card for each product type (for example Anarkali KPD Set) that is selling well right now. The best product is first.",
          "Each card is a ready design plan: which colours, fabrics and details to use, which mixes to make, what to make more of, and what to cut back.",
          "Everything on a card is about that one product only, so the parts of a card always agree with each other.",
          "\"In short\" at the top is a summary of the cards.",
        ],
      },
      {
        heading: "Reading a card",
        points: [
          "Top line — the product name, its category above it (for example ETHNIC), its trend (▲ Trending or ● In trend), and how good a choice it is: Strong, Good or Try small.",
          "Grey line — real sales facts: how much of our total sales it makes, how many of its designs are selling, how well one of its designs sells compared with an average design, and how it did in this same season last year.",
          "Use these — the colours, fabrics and details that sell best for this product right now. A green outline means \"make more of this\".",
          "Best mixes — the best colour + fabric pairs, made only from the colours and fabrics listed above. \"Made before\" = we already have designs like this (safer). \"New idea\" = never made before, so start small.",
          "＋ Make more — sells much better than other designs of this product, but there are only a few designs of it. Room to add more.",
          "－ Cut back — colours or fabrics of this product that are slowing down, no longer popular, or have too many designs that each sell little.",
          "Each card has its own About button that explains that card's numbers.",
        ],
      },
      {
        heading: "How we work it out",
        points: [
          "This season: mostly what is selling in the last month, and whether it is going up or down.",
          "Next season: we also look at how it sold in the same season last year.",
          "Only items that are ▲ Trending or ● In trend are suggested.",
          "We show Strong / Good / Try small instead of exact numbers, because the system is good at telling which choice is better, but not at guessing the exact pieces.",
        ],
      },
    ],
  },
  drivers: {
    title: "What drives sales this season",
    sections: [
      {
        heading: "What it shows",
        points: [
          "Which design features help sales (green, \"Lifts sales\") and which hurt sales (red, \"Holds sales back\") in this season.",
          "Example: \"V Neck +20%\" means a design with a V neck usually sells about 20% more than the same design with another neck.",
        ],
      },
      {
        heading: "How we work it out",
        points: [
          "The system looks at each feature on its own — for example only the neck — while everything else about the design stays the same.",
          "So it shows the effect of that one feature, not of the other features it usually comes with.",
        ],
      },
      {
        heading: "What to do",
        points: [
          "Use the green features in new designs and avoid the red ones.",
          "Small numbers (under 10%) make little difference — don't worry about them.",
        ],
      },
    ],
  },
};
