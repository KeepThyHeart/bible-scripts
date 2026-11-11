# KJV pronunciation review: conventions

Goal: the KJV will be read aloud by a TTS voice (Piper/Kokoro) that is fed espeak-ng **en-us** IPA.
We store only **corrections**: places where espeak's IPA would be heard as a mispronunciation.
Target: **American English**, **common American Protestant reading-aloud tradition** (as a pastor or
lector reading the KJV aloud from the pulpit would say it; standard American Bible pronouncing guides).

## IPA style (must match espeak-ng en-us output exactly in style)

- Allowed symbols ONLY (anything else is silently dropped by the voice):
  `a b d e f h i j k l m n o p r s t u v w x z æ ð ŋ ɐ ɑ ɔ ə ɚ ɛ ɜ ɡ ɪ ɹ ɾ ʃ ʊ ʌ ʒ ʔ ˈ ˌ ː ̩ θ ᵻ`
  (note: `ɡ` is U+0261, not ASCII g; use `ɹ` for English r, `dʒ`/`tʃ` for j/ch sounds).
- Stress marks `ˈ` (primary) and `ˌ` (secondary) go **immediately before the stressed vowel**, not at the
  syllable start: espeak writes `dʒˈeɪkəb` (Jacob), `mˈɛltʃ...`, `ɹᵻbˈɛl`. Follow that.
- Long vowels as espeak writes them: `iː uː ɑː ɔː ɜː`; diphthongs `eɪ aɪ ɔɪ aʊ oʊ`; r-coloured `ɚ` (unstressed),
  stressed "er" as `ˈɜː` (earth `ˈɜːθ`), "air" `ɛɹ`, "ear" `ɪɹ`, "or" `ɔːɹ`, "ar" `ɑːɹ`. Reduced i is `ᵻ`.
- Look at the espeak IPA of nearby common words in your batch to copy the style.

## What counts as a correction

Report it if a listener would hear the word as wrong: wrong stress, wrong vowel quality, wrong consonant,
missing/extra syllable, a name said in a way no American reader would say it. Do NOT report cosmetic
differences that sound fine (`ɾ` vs `t`, `ə` vs `ʌ`/`ɐ` unstressed, `ᵻ` vs `ɪ`, small vowel-length differences).
When espeak is acceptable, say nothing. Precision matters more than volume: when unsure whether
espeak is wrong, leave it.

## Known archaic forms (apply; extend the same pattern to their family)

hast `hˈæst`, saith `sˈɛθ`, shew `ʃˈoʊ` (shewed `ʃˈoʊd`, shewn, shewing, sheweth, shewbread `ʃˈoʊbɹɛd`),
wast `wˈɑːst`, wert `wˈɜːt`, doth `dˈʌθ`, -eth endings `ᵻθ`, -est endings `ᵻst`, Selah `sˈiːlə`,
Jehovah `dʒᵻhˈoʊvə`. Check thee/thou/thy/thine/ye/hath/hadst/didst/wilt/shalt/spake/sware/clave/brasen/
holpen/wist/wot/trow/ye etc. for plausibility.

## Names

- Use the American Protestant tradition. Decide obscure names yourself, consistently (same element = same
  sound: -iah `ˈaɪə`, Beth- `bˈɛθ`, -el `ɛl`, Ch usually `k` (Chedorlaomer, Chaldea), etc.).
- **Only** for well-known names whose pronunciation is genuinely disputed among American readers (a typical
  churchgoer would know the name and might notice), ALSO add an entry to your questions file with the options.
  Still give your default in the main file. Expect only a handful per batch, often zero.
- Hyphenated compounds (Beth-el, Abel-meholah) are split at hyphens in the word list; the context shows the
  compound. Give the fragment's IPA as spoken; if the compound as a whole needs it (stress across parts),
  emit an entry whose `word` is the whole lower-case compound with the hyphen (`"beth-el"`).
- Possessives (`abraham's`) follow the base name + `z`/`s`/`ᵻz`.

## Homograph senses (fixed IPA; use exactly these)

| word | sense A | sense B |
|---|---|---|
| read | present `ɹˈiːd` | past/participle `ɹˈɛd` |
| lead | verb guide `lˈiːd` | metal `lˈɛd` |
| live | verb `lˈɪv` | adjective (live goat/coal) `lˈaɪv` |
| lives | noun `lˈaɪvz` | verb `lˈɪvz` |
| wind / winds | noun air `wˈɪnd` / `wˈɪndz` | verb twist `wˈaɪnd` / `wˈaɪndz` |
| winding | (stairs, turning) `wˈaɪndɪŋ` | |
| wound | injury/injure `wˈuːnd` | past of wind (wrapped) `wˈaʊnd` |
| tear / tears | rend `tˈɛɹ` / `tˈɛɹz` | weeping `tˈɪɹ` / `tˈɪɹz` |
| bow / bows | weapon, rainbow `bˈoʊ` / `bˈoʊz` | bend down `bˈaʊ` / `bˈaʊz` |
| sow / sows | scatter seed `sˈoʊ` / `sˈoʊz` | female pig `sˈaʊ` |
| close | adj/adv near, secret `klˈoʊs` | verb shut `klˈoʊz` |
| closer | `klˈoʊsɚ` | |
| present / presents | noun/adj `pɹˈɛzənt` / `pɹˈɛzənts` | verb `pɹᵻzˈɛnt` / `pɹᵻzˈɛnts` |
| refuse | verb `ɹᵻfjˈuːz` | noun rubbish `ɹˈɛfjuːs` |
| record / records | noun (bear record) `ɹˈɛkɚd` / `ɹˈɛkɚdz` | verb `ɹᵻkˈɔːɹd` / `ɹᵻkˈɔːɹdz` |
| use / uses | noun `jˈuːs` / `jˈuːsᵻz` | verb `jˈuːz` / `jˈuːzᵻz` |
| abuse | noun `ɐbjˈuːs` | verb `ɐbjˈuːz` |
| excuse | noun `ɛkskjˈuːs` | verb `ɛkskjˈuːz` |
| object | noun `ˈɑːbdʒɛkt` | verb `əbdʒˈɛkt` |
| subject | noun/adj `sˈʌbdʒɛkt` | verb `səbdʒˈɛkt` |
| content | adj satisfied `kəntˈɛnt` | noun `kˈɑːntɛnt` |
| desert / deserts | wilderness `dˈɛzɚt` / `dˈɛzɚts` | what one deserves `dᵻzˈɜːt` / `dᵻzˈɜːts` |
| entrance | noun `ˈɛntɹəns` | |
| produce | verb `pɹədˈuːs` | noun `pɹˈoʊduːs` |
| rebel / rebels | noun `ɹˈɛbəl` / `ɹˈɛbəlz` | verb `ɹᵻbˈɛl` / `ɹᵻbˈɛlz` |
| conduct | verb `kəndˈʌkt` | noun `kˈɑːndʌkt` |
| convert / converts | verb `kənvˈɜːt` / `kənvˈɜːts` | noun `kˈɑːnvɜːt` / `kˈɑːnvɜːts` |
| perfect | adjective `pˈɜːfɛkt` | verb make perfect `pɚfˈɛkt` |
| separate | adjective `sˈɛpɹət` | verb `sˈɛpɚɹeɪt` |
| frequent | adjective `fɹˈiːkwənt` | verb `fɹiːkwˈɛnt` |
| lower | comparative of low `lˈoʊɚ` | frown/darken (lour) `lˈaʊɚ` |
| blessed | adjective, incl. "Blessed are/is/be", "the blessed", "blessed Lord" `blˈɛsᵻd` | finite verb or perfect ("God blessed them", "hath blessed", "was blessed") `blˈɛst` |
| cursed | adjective/predicate exclamation ("Cursed be/is", "cursed children") `kˈɜːsᵻd` | finite verb or perfect ("he cursed", "hath cursed") `kˈɜːst` |
| learned | adjective scholarly `lˈɜːnᵻd` | verb `lˈɜːnd` |
| beloved | always `bᵻlˈʌvᵻd` | |
| aged | adjective old (aged men) `ˈeɪdʒᵻd` | verb `ˈeɪdʒd` |
| presented, closer, winding | single readings as above (presented `pɹᵻzˈɛntᵻd`) | |

Capitalised forms take the same IPA. Possessive or capitalised sentence-initial forms are the same word.
