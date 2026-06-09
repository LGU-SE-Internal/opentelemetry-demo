package v1

type EvaluateRequest struct {
	FlagKey string            `protobuf:"bytes,1,opt,name=flag_key,json=flagKey,proto3" json:"flag_key,omitempty"`
	UserId  string            `protobuf:"bytes,2,opt,name=user_id,json=userId,proto3" json:"user_id,omitempty"`
	Context map[string]string `protobuf:"bytes,3,rep,name=context,proto3" json:"context,omitempty" protobuf_key:"bytes,1,opt,name=key,proto3" protobuf_val:"bytes,2,opt,name=value,proto3"`
}
