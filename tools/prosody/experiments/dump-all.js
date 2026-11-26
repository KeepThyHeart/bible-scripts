// Scratch: the exact phoneme strings the pipeline synthesizes, for every verse of the module.
const os=require('os'),path=require('path'),fs=require('fs');
const core=require('./dist/core.js');
const {kokoroDefinition,splitForKokoro}=require('./dist/engines/kokoro.js');
const {openModuleText}=require('./dist/moduleSource.js');
const {loadPronunciation}=require('./dist/pronunciation.js');
const {prepareChapterText}=require('./dist/text.js');
const [pronDir,out]=process.argv.slice(2);
(async()=>{
  const mod=openModuleText(path.join(os.homedir(),'bible-audio/online/bible_kjv.db'));
  const {overlay}=loadPronunciation(pronDir,'en-us','KJV');
  const voice='am_michael:0.5+bm_george:0.5';
  const eng=kokoroDefinition.create({cacheDir:path.join(os.homedir(),'.cache/bible-audio-build'),threads:1,quantization:'q8',clearSpeech:true});
  await eng.prepare(voice,()=>{});
  const tts=eng.tts, orig=tts.tokenizer, STOP=Symbol(); let got;
  const wrap=Object.assign((p)=>{got=p;throw STOP;},orig); Object.setPrototypeOf(wrap,orig); tts.tokenizer=wrap;
  const fd=fs.openSync(out,'w'); let n=0, t0=Date.now();
  for(const [book,chs] of mod.chapters()) for(const chapter of chs){
    const pr=(t,v)=>core.applyPronunciation(t,overlay,v===null?undefined:core.pronunciationVerseKey(book,chapter,v));
    const prep=prepareChapterText(new core.TextPreparer(),{moduleAbbr:'KJV',book,chapter,bookName:mod.bookName(book),language:mod.language,verses:mod.verses(book,chapter),pronounce:pr});
    for(const v of [...(prep.intro?[{verse:0,text:prep.intro}]:[]),...prep.verses]){
      const pieces=splitForKokoro(v.text);
      for(let i=0;i<pieces.length;i++){ got=undefined; try{await eng.generate(pieces[i],voice,0.9);}catch(e){if(e!==STOP)throw e;}
        fs.writeSync(fd,JSON.stringify({book,chapter,verse:v.verse,piece:i,text:pieces[i],phonemes:got})+'\n'); }
    }
    if(++n%50===0) console.log(n,'chapters',Math.round((Date.now()-t0)/1000)+'s');
  }
  fs.closeSync(fd); console.log('done',n,'chapters',Math.round((Date.now()-t0)/1000)+'s');
})().catch(e=>{console.error(e);process.exit(1)});
