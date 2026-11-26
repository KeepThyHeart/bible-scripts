// Scratch: dump the exact phoneme strings the pipeline would synthesize for one chapter.
const os=require('os'),path=require('path'),fs=require('fs');
const core=require('./dist/core.js');
const {kokoroDefinition,splitForKokoro}=require('./dist/engines/kokoro.js');
const {openModuleText}=require('./dist/moduleSource.js');
const {loadPronunciation}=require('./dist/pronunciation.js');
const {prepareChapterText}=require('./dist/text.js');
const [book,chapter,pronDir,out]=[+process.argv[2],+process.argv[3],process.argv[4],process.argv[5]];
(async()=>{
  const mod=openModuleText(path.join(os.homedir(),'bible-audio/online/bible_kjv.db'));
  const {overlay}=loadPronunciation(pronDir,'en-us','KJV');
  const pr=(t,v)=>core.applyPronunciation(t,overlay,v===null?undefined:core.pronunciationVerseKey(book,chapter,v));
  const prep=prepareChapterText(new core.TextPreparer(),{moduleAbbr:'KJV',book,chapter,bookName:mod.bookName(book),language:mod.language,verses:mod.verses(book,chapter),pronounce:pr});
  const voice='am_michael:0.5+bm_george:0.5';
  const eng=kokoroDefinition.create({cacheDir:path.join(os.homedir(),'.cache/bible-audio-build'),threads:1,quantization:'q8',clearSpeech:true});
  await eng.prepare(voice,()=>{});
  const tts=eng.tts, orig=tts.tokenizer, STOP=Symbol(); let got;
  const wrap=Object.assign((p)=>{got=p;throw STOP;},orig); Object.setPrototypeOf(wrap,orig);
  tts.tokenizer=wrap;
  const rows=[];
  const items=[...(prep.intro?[{verse:0,text:prep.intro}]:[]),...prep.verses];
  for(const v of items){ const pieces=splitForKokoro(v.text);
    for(const p of pieces){ got=undefined; try{await eng.generate(p,voice,0.9);}catch(e){if(e!==STOP)throw e;}
      rows.push({verse:v.verse,text:p,phonemes:got}); } }
  fs.writeFileSync(out,rows.map(r=>JSON.stringify(r)).join('\n')+'\n'); console.log(rows.length,'pieces');
})().catch(e=>{console.error(e);process.exit(1)});
