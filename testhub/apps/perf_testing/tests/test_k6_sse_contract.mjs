import test from 'node:test';
import assert from 'node:assert/strict';
import { createEventContract, runSSESession } from '../engines/k6_sse.js';

test('blank fragments are strictly strings and never replace a meaningful CHAT answer', () => {
  const event = (content, phase='report', session='owned-session', finish='stop') => ({name:'message',data:JSON.stringify({session_id:session,choices:[{delta:{phase,content},finish_reason:finish}]})});
  for (const blank of ['', ' \t\r\n', '\u3000']) {
    const config=sseTemplate('CHAT'), state=createEventContract(config,{session_id:'owned-session'});
    assert.equal(state.accept(event(blank)), 'continue');
    assert.equal(state.accept(event('PRIVATE_ANSWER')), 'continue');
    assert.equal(state.accept(event('', 'finish')), 'continue');
    assert.equal(state.accept({name:'message',data:'[DONE]'}), 'complete');
    const empty=createEventContract(config,{session_id:'owned-session'});
    assert.equal(empty.accept(event(blank)), 'continue');
    assert.equal(empty.accept(event('', 'finish')), 'error');
    assert.equal(empty.completed(), false);
  }
  for (const value of [null, false, 0, [], {}, undefined]) {
    const state=createEventContract(sseTemplate(),{session_id:'owned-session'});
    assert.equal(state.accept(event(value)), 'error');
  }
});

test('failure locations contain only stable codes and one-based contract positions', () => {
  const config=sseTemplate(), state=createEventContract(config,{session_id:'PRIVATE_SESSION'});
  const message=(phase,session='PRIVATE_SESSION',finish='stop')=>({name:'message',data:JSON.stringify({session_id:session,choices:[{delta:{phase,content:'PRIVATE_ANSWER'},finish_reason:finish}]})});
  assert.equal(state.accept(message('report')), 'continue');
  assert.equal(state.accept(message('finish','PRIVATE_SESSION','PRIVATE_BAD')), 'error');
  assert.deepEqual(state.diagnostic(), {event_index:2,scope:'rule_assertion',rule_index:4,condition_index:1});
  const wrong=createEventContract(config,{session_id:'PRIVATE_SESSION'});
  wrong.accept(message('report','PRIVATE_WRONG'));
  assert.deepEqual(wrong.diagnostic(),{event_index:1,scope:'global_assertion',condition_index:1});
  assert.ok(!JSON.stringify([state.diagnostic(),wrong.diagnostic()]).includes('PRIVATE'));
});

test('old saved nonempty contracts retain their strict behavior and location', () => {
  const config=make();
  config.rules[0].assertions=[{type:'JSON_PATH',expr:'$.text',operator:'nonempty'}];
  const state=createEventContract(config,{});
  assert.equal(state.accept({name:config.rules[0].event,data:JSON.stringify({text:' \t'})}), 'error');
  assert.equal(state.reason(), 'assertion_failed');
  assert.deepEqual(state.diagnostic(), {event_index:1,scope:'rule_assertion',rule_index:1,condition_index:1});
});

test('transport deadline wins over a callback diagnostic and EOF never gains success', () => {
  for (const reason of ['idle_timeout','total_timeout','unexpected_eof','event_error']) {
    const result=runSSESession(sseTemplate(),{context:{session_id:'s'},remaining:()=>1000,emit:()=>{},open:()=>({ok:false,started:true,closed:true,reason,events:1,bytes:8,first_event_ms:1})});
    assert.equal(result.ok,false);
    assert.equal(result.reason,reason);
    assert.deepEqual(result.diagnostic,{scope:'transport',event_index:1});
  }
});
import { sseTemplate } from '../../../frontend/src/views/performance-testing/sseStepForm.mjs';

const eq = (expr, expected) => ({type:'JSON_PATH',expr,operator:'eq',expected});
const make = () => ({assertions:[],error_conditions:[],rules:[
  {name:'meta',event:'meta',match:[],assertions:[eq('$.ok',true)],extractors:[{name:'sid',expr:'$.id'}],min_events:1,max_events:1,after:[],terminal:false},
  {name:'segment',event:'segment',match:[],assertions:[eq('$.id','{{sid}}')],extractors:[],min_events:1,max_events:3,after:['meta'],terminal:false,sequence:{expr:'$.seq',start:1}},
  {name:'done',event:'done',match:[],assertions:[{type:'JSON_PATH',expr:'$.segments',operator:'eq',expected_count:'segment'}],extractors:[],min_events:1,max_events:1,after:['segment'],terminal:true}
]});
const frame = (name, data) => ({name,data:JSON.stringify(data)});
test('ordered event bindings, sequence and terminal counts are required',()=>{
  const state=createEventContract(make(),{});
  assert.equal(state.accept(frame('meta',{ok:true,id:'session'})),'continue');
  assert.equal(state.accept(frame('segment',{id:'session',seq:1})),'continue');
  assert.equal(state.accept(frame('done',{segments:1})),'complete');
  assert.deepEqual({...state.outputs()},{sid:'session'});
});
test('wrong order, sequence, assertion or missing terminal is never a pass',()=>{
  for(const [events,reason] of [
    [[frame('segment',{id:'session',seq:1})],'event_order'],
    [[frame('meta',{ok:true,id:'session'}),frame('segment',{id:'other',seq:1})],'assertion_failed'],
    [[frame('meta',{ok:true,id:'session'}),frame('segment',{id:'session',seq:2})],'sequence'],
    [[frame('meta',{ok:true,id:'session'}),frame('done',{segments:0})],'event_order'],
    [[{name:'meta',data:'not json'}],'invalid_json'],
  ]){
    const state=createEventContract(make(),{});
    for(const event of events) state.accept(event);
    assert.equal(state.reason(),reason);
  }
});
test('business errors before a terminal and bounded extraction stay private',()=>{
  const config=make();config.error_conditions=[eq('$.failed',true)];
  const state=createEventContract(config,{});
  assert.equal(state.accept(frame('meta',{failed:true,message:'PRIVATE_BODY'})),'error');
  assert.equal(state.reason(),'business_error');
  assert.ok(!JSON.stringify(state.outputs()).includes('PRIVATE_BODY'));
  const large=createEventContract(make(),{});
  assert.equal(large.accept(frame('meta',{ok:true,id:'x'.repeat(4097)})),'error');
  assert.equal(large.reason(),'extraction_failed');
});

const chatFrame = (phase, content='', session='owned-session') => frame('message', {
  session_id:session, choices:[{delta:{phase,content},finish_reason:phase==='finish'?'stop':null}]
});
test('chat preset requires owned nonempty answer, successful finish, then sentinel',()=>{
  const state=createEventContract(sseTemplate('CHAT'),{session_id:'owned-session'});
  for(const event of [chatFrame('progress','thinking'),chatFrame('report','actual answer'),chatFrame('finish')]) assert.equal(state.accept(event),'continue');
  assert.equal(state.accept({name:'message',data:'[DONE]'}),'complete');
  for(const event of [{name:'message',data:'[DONE]'},chatFrame('error','upstream failed'),chatFrame('report','answer','another-session'),chatFrame('finish')]){
    const failed=createEventContract(sseTemplate('CHAT'),{session_id:'owned-session'});
    assert.equal(failed.accept(event),'error');
  }
});
test('message speech checks meta ownership, stable id, one-based sequence and exact terminal count',()=>{
  const config=sseTemplate('MESSAGE_SPEECH');
  const state=createEventContract(config,{session_id:'s',data_id:'d'});
  assert.equal(state.accept(frame('meta',{session_id:'s',data_id:'d',speech_id:'sp',format:'mp3',total_chars:2,units:1})),'continue');
  assert.equal(state.accept(frame('segment',{speech_id:'sp',seq:1,chars:2,duration_ms:20,audio_base64:'YWI='})),'continue');
  assert.equal(state.accept(frame('done',{speech_id:'sp',segments:1,total_chars:2,units:1})),'complete');
});
test('content speech supports done-only cache and zero-based synthesis, rejects false cache or missing audio',()=>{
  const config=sseTemplate('CONTENT_SPEECH');
  const terminal=cached=>frame('done',{cached,audio_url:'https://fixture.invalid/audio.mp3?signature=synthetic',duration_ms:20,total_chars:2,audio_version:'v1'});
  const segment=frame('segment',{seq:0,chars:2,duration_ms:20,audio_b64:'YWI='});
  const cached=createEventContract(config,{});
  assert.equal(cached.accept(terminal(true)),'complete');
  const fresh=createEventContract(config,{});
  assert.equal(fresh.accept(segment),'continue');
  assert.equal(fresh.accept(terminal(false)),'complete');
  const noAudio=createEventContract(config,{});
  assert.equal(noAudio.accept(terminal(false)),'error');
  const contradicted=createEventContract(config,{});
  assert.equal(contradicted.accept(segment),'continue');
  assert.equal(contradicted.accept(terminal(true)),'error');
});

test('audio URL requires valid authority and port while preserving signed queries and IPv6',()=>{
  for (const [url, valid] of [
    ['https://:invalid/audio.mp3',false],['https://host:NOT_A_PORT/audio.mp3',false],
    ['https://host:99999/audio.mp3',false],['https://host:0/audio.mp3',false],
    ['https://[::1]:8443/audio.mp3?signature=abc%2Fdef',true],
    ['https://files.fixture.invalid/audio.mp3?Expires=20&Signature=abc%2Fdef',true],
    ['https://[:::]/audio.mp3',false],['https://user:pass@host/audio.mp3',false],
  ]) {
    const state=createEventContract(sseTemplate('CONTENT_SPEECH'),{});
    assert.equal(state.accept(frame('done',{cached:true,audio_url:url,duration_ms:20,total_chars:2,audio_version:'v1'})),valid?'complete':'error',url);
  }
});

test('native first event is retained for named errors without double counting callbacks',()=>{
  for(const callback of [false,true]){
    const events=[];
    runSSESession(make(),{context:{},url:'http://fixture.invalid/',method:'GET',headers:{},remaining:()=>1000,
      emit:event=>events.push(event),open:(_url,_options,accept)=>{
        if(callback) accept(frame('meta',{ok:true,id:'session'}));
        return {ok:false,started:true,closed:true,reason:'event_error',status:200,events:1,bytes:20,elapsed_ms:9,first_event_ms:3};
      }});
    assert.equal(events.length,1);
    if(!callback) assert.equal(events[0].elapsed_ms,3);
  }
});
