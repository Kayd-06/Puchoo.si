// Local selector copy must remain available even when the translation service is down.
const COPY = {
  en: ['analyses', 'Analysis', 'Choose an analysis to view its answer.', 'Separate analyses'],
  hi: ['विश्लेषण', 'विश्लेषण', 'उत्तर देखने के लिए कोई विश्लेषण चुनें।', 'अलग-अलग विश्लेषण'],
  bn: ['বিশ্লেষণ', 'বিশ্লেষণ', 'উত্তর দেখতে একটি বিশ্লেষণ বেছে নিন।', 'পৃথক বিশ্লেষণ'],
  gu: ['વિશ્લેષણ', 'વિશ્લેષણ', 'જવાબ જોવા માટે વિશ્લેષણ પસંદ કરો.', 'અલગ વિશ્લેષણ'],
  kn: [
    'ವಿಶ್ಲೇಷಣೆಗಳು',
    'ವಿಶ್ಲೇಷಣೆ',
    'ಉತ್ತರವನ್ನು ನೋಡಲು ಒಂದು ವಿಶ್ಲೇಷಣೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ.',
    'ಪ್ರತ್ಯೇಕ ವಿಶ್ಲೇಷಣೆಗಳು',
  ],
  ml: ['വിശകലനങ്ങൾ', 'വിശകലനം', 'ഉത്തരം കാണാൻ ഒരു വിശകലനം തിരഞ്ഞെടുക്കുക.', 'വേറിട്ട വിശകലനങ്ങൾ'],
  mr: ['विश्लेषणे', 'विश्लेषण', 'उत्तर पाहण्यासाठी एक विश्लेषण निवडा.', 'स्वतंत्र विश्लेषणे'],
  pa: ['ਵਿਸ਼ਲੇਸ਼ਣ', 'ਵਿਸ਼ਲੇਸ਼ਣ', 'ਜਵਾਬ ਦੇਖਣ ਲਈ ਇੱਕ ਵਿਸ਼ਲੇਸ਼ਣ ਚੁਣੋ।', 'ਵੱਖਰੇ ਵਿਸ਼ਲੇਸ਼ਣ'],
  ta: [
    'பகுப்பாய்வுகள்',
    'பகுப்பாய்வு',
    'பதிலைக் காண ஒரு பகுப்பாய்வைத் தேர்ந்தெடுக்கவும்.',
    'தனித்தனி பகுப்பாய்வுகள்',
  ],
  te: [
    'విశ్లేషణలు',
    'విశ్లేషణ',
    'సమాధానం చూడటానికి ఒక విశ్లేషణను ఎంచుకోండి.',
    'వేర్వేరు విశ్లేషణలు',
  ],
  od: ['ବିଶ୍ଳେଷଣ', 'ବିଶ୍ଳେଷଣ', 'ଉତ୍ତର ଦେଖିବାକୁ ଏକ ବିଶ୍ଳେଷଣ ବାଛନ୍ତୁ।', 'ପୃଥକ ବିଶ୍ଳେଷଣ'],
  ur: ['تجزیے', 'تجزیہ', 'جواب دیکھنے کے لیے ایک تجزیہ منتخب کریں۔', 'الگ الگ تجزیے'],
};
const SCRIPTS = [
  [/\p{Script=Bengali}/u, 'bn'],
  [/\p{Script=Gujarati}/u, 'gu'],
  [/\p{Script=Kannada}/u, 'kn'],
  [/\p{Script=Malayalam}/u, 'ml'],
  [/\p{Script=Gurmukhi}/u, 'pa'],
  [/\p{Script=Tamil}/u, 'ta'],
  [/\p{Script=Telugu}/u, 'te'],
  [/\p{Script=Oriya}/u, 'od'],
  [/\p{Script=Arabic}/u, 'ur'],
  [/\p{Script=Devanagari}/u, 'hi'],
];
export function analysisLabels(text, languageCode) {
  const hint = typeof languageCode === 'string' ? languageCode.toLowerCase().split(/[-_]/)[0] : '';
  const language = hint === 'or' ? 'od' : hint;
  const detected = SCRIPTS.find(([script]) => script.test(text))?.[1] || 'en';
  const [plural, singular, instruction, region] = COPY[language] || COPY[detected];
  return {
    plural,
    singular,
    instruction,
    region,
    direction: (COPY[language] ? language : detected) === 'ur' ? 'rtl' : 'auto',
  };
}
