/** @typedef {{id:number,name:string,initial:object,records:object[],description:string,segmented:boolean,segments:object[],archive:object}} LogSet */
/** @typedef {{id:number,name:string,log_set_id:number,crash_after:number}} CrashCase */
/** @typedef {{id:number,crash_case_id:number|null,label:string,status:string,target_lsn:number,snapshot:object,result:object|null}} Recovery */
export {};
