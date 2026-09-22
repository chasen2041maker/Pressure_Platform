import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createReminderGuard } from '../engines/k6_reminder_recovery.js';

const hash = value => createHash('sha256').update(value).digest('hex');
function fixture() {
  const path = '/api/v1/portfolio/reminders/sz000001';
  const names=['price_above','price_below','daily_pct_up','daily_pct_down','five_min_pct_up','five_min_pct_down'];
  const conditions=Object.fromEntries(names.map(name=>[name,{enabled:false,value:'0'}]));
  conditions.price_above={enabled:true,value:'1000000'};
  conditions.price_below={enabled:false,value:'12.50'};
  const body=JSON.stringify({conditions,policy:{channels:{app_push:false,message_center:true},
    frequency:{mode:'cooldown',interval_minutes:30},validity:{mode:'permanent',valid_until:null},trading_session_only:true},precondition:{mode:'absent'}});
  const context = { rr_owner:'100', token:'private-token', rr_token_hash:hash('private-token'),
    rr_put_key:'rr1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1-put',rr_delete_key:'rr1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1-delete',rr_put_body:body };
  context.rr_put_fingerprint = hash(`PUT\n${path}\n\n${body}`);
  context.rr_put_digest = hash(`PUT\n${path}\nuser:100\n${context.rr_put_key}`);
  context.rr_delete_body = JSON.stringify({put_request_key:context.rr_put_key,put_request_fingerprint:context.rr_put_fingerprint});
  context.rr_delete_fingerprint = hash(`DELETE\n${path}\n\n${context.rr_delete_body}`);
  context.rr_delete_digest = hash(`DELETE\n${path}\nuser:100\n${context.rr_delete_key}`);
  const binding = {version:1,path,origin:'http://127.0.0.1:12345',token_variable:'token',
    config:{stock_code:'sz000001',put_step_id:11,receipt_step_id:12,get_step_id:13,delete_step_id:14}};
  return {context,binding,guard:createReminderGuard(binding,context,hash)};
}
test('unverified owner cannot send a PUT; exact owner and token proof enables exact frozen bytes', () => {
  const {guard,context,binding} = fixture();
  assert.throws(()=>guard.request({id:11,method:'PUT'},binding.origin+binding.path),/Reminder/);
  assert.equal(guard.response({id:'reminder:identity'},{code:'OK',data:{user_id:100}}),false);
  assert.equal(guard.response({id:'reminder:identity'},{code:'OK',data:{user_id:'100'}}),true);
  assert.equal(guard.request({id:11,method:'PUT'},binding.origin+binding.path).body,context.rr_put_body);
  context.rr_put_body += ' ';
  assert.throws(()=>guard.request({id:11,method:'PUT'},binding.origin+binding.path),/Reminder/);
});
test('receipt requires exact source digest fingerprint owner-bound scope and string ID', () => {
  const {guard,context,binding} = fixture();
  guard.response({id:'reminder:identity'},{code:'OK',data:{user_id:'100'}});
  const body=JSON.parse(context.rr_put_body);
  for(const [name,row] of Object.entries(body.conditions)) row.value=row.value==='0'?'':name==='price_above'?'1000000.000000':'12.500000';
  const receipt={request_key:context.rr_put_digest,request_fingerprint:context.rr_put_fingerprint,
    operation:'put',stock_code:binding.config.stock_code,state:'committed',
    rule:{id:'9223372036854775807',revision:1,conditions:body.conditions,policy:body.policy}};
  assert.equal(guard.response({id:12},{code:'OK',data:receipt}),true);
  assert.equal(guard.response({id:14},{code:'OK',data:{outcome:'deleted',deleted:true,rule_id:receipt.rule.id,before_revision:1,after_revision:2}}),true);
  for(const change of [r=>r.rule.revision='1',r=>r.rule.id=100,r=>r.request_fingerprint='0'.repeat(64)]) {
    const value=structuredClone(receipt);change(value);
    assert.equal(guard.response({id:12},{code:'OK',data:value}),false);
  }
  for(const value of ['1000000.000001','1e6',1000000,'01000000','999999.999999']) {
    const changed=structuredClone(receipt);changed.rule.conditions.price_above.value=value;
    assert.equal(guard.response({id:12},{code:'OK',data:changed}),false);
  }
});
test('ordinary steps bypass the domain guard and never expose frozen material', () => {
  const {guard}=fixture();
  assert.equal(guard.request({id:50,method:'GET'},'http://elsewhere.invalid/'),null);
  assert.equal(guard.response({id:50},{}),true);
});
