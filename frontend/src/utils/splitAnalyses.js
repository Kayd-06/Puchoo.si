// Split only explicit, standalone questions. Keep dependent follow-ups together.
// A new question may start right after "?" even without a space ("month?Among").
const QUESTION_BREAK =
  /(?<=[?؟？])\s*(?=\p{L})|(?<=[.।॥])\s*(?=for each\b|हर महीने|प्रत्येक|প্রতি মাসে|દર મહિને|ಪ್ರತಿ ತಿಂಗಳ|ഓരോ മാസവും|दर महिन्या|ਹਰ ਮਹੀਨੇ|ஒவ்வொரு மாதமும்|ప్రతి నెల|ପ୍ରତି ମାସ|ہر مہینے)/iu;
const QUESTION_END = /[?؟？]$/u;
const SENTENCE_END = /[?.؟？।॥]$/u;
const REPORT_START =
  /^(?:for each\b|हर महीने|प्रत्येक|প্রতি মাসে|દર મહિને|ಪ್ರತಿ ತಿಂಗಳ|ഓരോ മാസവും|दर महिन्या|ਹਰ ਮਹੀਨੇ|ஒவ்வொரு மாதமும்|ప్రతి నెల|ପ୍ରତି ମାସ|ہر مہینے)/iu;
// A monthly instruction ending in a full stop is not another question.
const NATIVE_INTERROGATIVE =
  /(?:कितना|कितनी|कितने|क्या|कौन|किती|কত|কোন|કેટલ|કયા|ಎಷ್ಟು|ಯಾವ|എത്ര|ഏത്|ਕਿੰਨ|ਕਿਹੜ|எவ்வளவு|எந்த|ఎంత|ఏది|କେତେ|କେଉଁ|کتن|کون)/u;
// Fragments that continue the previous question instead of starting a new one.
const CONTINUATION_START = /^(?:or|and|but|also|then|else|plus)\b/i;
// References to an earlier answer anywhere in a later part.
const BACK_REFERENCE =
  /\b(it|its|they|them|these|those|above|previous (?:result|answer|question|analysis)|same|that (?:customer|region|result|class))\b/i;
// "their" refers back only when it opens the question, e.g. "What is their average?".
// Later uses ("customers ... in their region") point at the part's own subject.
const LEADING_THEIR = /^(?:\S+\s+){0,3}their\b/i;

// Conservative native-language references keep a dependent follow-up in the
// same query, where the planner still has the earlier question's context.
const NATIVE_REFERENCE =
  /(?<![\p{L}\p{M}])(?:उसका|उसकी|उसके|उनका|उनकी|उसी|इसका|इसकी|इनका|त्याचा|त्याची|त्याचे|त्यांची|त्याची|তাদের|তার|এগুলোর|તેની|તેના|તેમની|ಅದರ|ಅವುಗಳ|അതിന്റെ|അവയുടെ|ਉਸਦਾ|ਉਸਦੀ|ਉਨ੍ਹਾਂ|அதன்|அவற்றின்|దాని|వాటి|దాని యొక్క|ତାହାର|ସେମାନଙ୍କ|اس کا|اس کی|ان کا|ان کی)(?![\p{L}\p{M}])/u;
const NATIVE_CONTINUATION =
  /^(?:या|और|लेकिन|फिर|किंवा|आणि|पण|অথবা|এবং|কিন্তু|અથવા|અને|પરંતુ|ಅಥವಾ|ಮತ್ತು|ಆದರೆ|അല്ലെങ്കിൽ|കൂടാതെ|പക്ഷേ|ਜਾਂ|ਅਤੇ|ਪਰ|அல்லது|மேலும்|ஆனால்|లేదా|మరియు|కానీ|କିମ୍ବା|ଏବଂ|କିନ୍ତୁ|یا|اور|لیکن)(?![\p{L}\p{M}])/u;

export function splitAnalyses(text) {
  const input = text.trim();
  const fragments = input
    .split(QUESTION_BREAK)
    .map((s) => s.trim())
    .filter(Boolean);
  const parts = [];
  for (const fragment of fragments) {
    const standalone =
      QUESTION_END.test(fragment) ||
      (REPORT_START.test(fragment) &&
        SENTENCE_END.test(fragment) &&
        NATIVE_INTERROGATIVE.test(fragment));
    if (
      parts.length &&
      (!standalone || CONTINUATION_START.test(fragment) || NATIVE_CONTINUATION.test(fragment))
    )
      parts[parts.length - 1] += ' ' + fragment;
    else parts.push(fragment);
  }
  if (parts.length < 2 || parts.length > 5 || parts.some((part) => !SENTENCE_END.test(part)))
    return [input];
  if (
    parts.some(
      (part, index) =>
        index > 0 &&
        (BACK_REFERENCE.test(part) || LEADING_THEIR.test(part) || NATIVE_REFERENCE.test(part)),
    )
  )
    return [input];
  return parts;
}
