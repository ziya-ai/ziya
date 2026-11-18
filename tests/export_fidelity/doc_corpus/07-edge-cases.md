---
ziya-doc: 1
title: "An Evaluation of Internationalisation Readiness Across the Customer-Facing Web and Mobile Surfaces for the 2027 Market Expansion"
author: "Localization Engineering and Product Globalization Working Group"
date: 2026-11-13
layout: report
page:
  margin: {top: 16mm, bottom: 18mm, left: 19mm, right: 19mm}
---

# Scope

This review covers the web application, the two mobile apps and transactional email. It was written to stress the layout: the title above is deliberately long, there is no subtitle, and the author line is long.

# Text in other scripts

The product name must not be translated. Examples of strings that currently overflow their containers:

- German: *Datenschutzeinstellungen zurücksetzen* and *Rechtsschutzversicherungsgesellschaften*.
- Japanese: 設定をリセットしてもよろしいですか？この操作は元に戻せません。
- Arabic: هل أنت متأكد أنك تريد إعادة تعيين الإعدادات؟
- Emoji in user content: 🚀 ✅ ⚠️ and flags 🇯🇵 🇩🇪.

A long unbroken token such as `com.example.checkout.payments.internal.gateway.TokenisationServiceConfigurationProperties` must wrap or shrink, and so must a long URL: https://example.com/docs/internationalisation/guides/pluralisation-rules-and-gender-agreement?locale=de-DE&version=2027.1&section=appendix-c

# Nested structure

1. Web application
   - Strings
     - 4,120 extracted; 310 still concatenated at run time.
     - Plural forms: only `one` and `other` are handled.
       1. Polish needs `few` and `many`.
       2. Arabic needs all six categories.
   - Layout
     - Right-to-left mirroring is untested.
2. Mobile apps
   - iOS uses the system formatter and is correct.
   - Android formats dates by hand in 14 places.
3. Email
   - Templates are English only.

Task list from the working group:

- [x] Inventory strings
- [x] Choose a message format (ICU)
- [ ] Pseudo-localisation in CI
- [ ] Right-to-left review

#### Fourth-level heading

Text under an h4, which the stylesheet sets as a small-caps run-in.

##### Fifth-level heading

Text under an h5.

# Quotations and notes

> Good localisation is invisible. The user should never be able to tell which language the product was written in first.

> **Note:** Pseudo-localisation replaces each string with an accented, lengthened copy, so truncation and hard-coded text show up before a translator is involved.

> **TODO:** Confirm the Arabic numeral policy with the regional team.

A paragraph with inline formatting: **bold**, *italic*, ~~struck~~, `inline code`, a [link](https://example.com), H~2~O and E = mc^2^, and a footnote reference.[^1]

[^1]: Footnotes are not part of the documented IR; this checks that one degrades sensibly.

---

# Wide content

A table with a long first column and a code column:

| Message key | Source string | Problem |
|---|---|---|
| `checkout.summary.items_in_basket_with_discount_applied` | You have {count} items in your basket and a {percent}% discount has been applied | Concatenated from three parts |
| `account.security.two_factor.recovery_codes.regenerate_confirmation` | Regenerate codes? | Too short to give translators context |
| `email.receipt.footer.legal` | © 2026 Example Ltd. All rights reserved. | Hard-coded year |

A code block with one very long line:

```text
2026-11-13T09:14:22.418Z ERROR i18n.MessageFormat - Missing plural category 'few' for key 'checkout.summary.items_in_basket_with_discount_applied' in locale 'pl-PL' (fallback 'other' used) request_id=7f3c2a9e-51d4-4c1b-9a0e-2f6b8d0c4e17
```

Display math next to prose: the expansion budget for a string of length $n$ is

$$w(n) = \begin{cases} 3n & n \le 10 \\ 2n & 10 < n \le 20 \\ 1.5\,n & n > 20 \end{cases}$$

# Closing

A final short section, to see where the document ends on the page.
