import { expect, it } from 'vitest';
import { analysisLabels } from '../analysisLanguage';

it.each([
  ['hi-IN', 'कितनी बिक्री?', 'विश्लेषण'],
  ['bn-IN', 'কত বিক্রয়?', 'বিশ্লেষণ'],
  ['gu-IN', 'કેટલું વેચાણ?', 'વિશ્લેષણ'],
  ['kn-IN', 'ಮಾರಾಟ ಎಷ್ಟು?', 'ವಿಶ್ಲೇಷಣೆ'],
  ['ml-IN', 'വിൽപ്പന എത്ര?', 'വിശകലനം'],
  ['pa-IN', 'ਕਿੰਨੀ ਵਿਕਰੀ?', 'ਵਿਸ਼ਲੇਸ਼ਣ'],
  ['ta-IN', 'விற்பனை எவ்வளவு?', 'பகுப்பாய்வு'],
  ['te-IN', 'అమ్మకాలు ఎంత?', 'విశ్లేషణ'],
  ['od-IN', 'ବିକ୍ରୟ କେତେ?', 'ବିଶ୍ଳେଷଣ'],
  ['ur-IN', 'فروخت کتنی؟', 'تجزیہ'],
])(
  'localizes %s typed and detected voice questions without a network request',
  (language, text, expected) => {
    expect(analysisLabels(text).singular).toBe(expected);
    expect(analysisLabels('romanized speech?', language).singular).toBe(expected);
  },
);
it('honors Marathi voice detection when scripts are shared with Hindi', () => {
  expect(analysisLabels('एकूण विक्री किती?', 'mr-IN').plural).toBe('विश्लेषणे');
});
it('supports aliases and uses a readable fallback for unknown language metadata', () => {
  expect(analysisLabels('ବିକ୍ରୟ କେତେ?', 'or-IN').singular).toBe('ବିଶ୍ଳେଷଣ');
  expect(analysisLabels('कितनी बिक्री?', 'auto').singular).toBe('विश्लेषण');
  expect(analysisLabels('What is revenue?', null).singular).toBe('Analysis');
  expect(analysisLabels('فروخت کتنی؟').direction).toBe('rtl');
});
