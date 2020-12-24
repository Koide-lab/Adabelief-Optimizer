import math
import torch
from torch.optim.optimizer import Optimizer
from collections import defaultdict

class AdaBelief(Optimizer):
    r"""Implements AdaBelief algorithm proposed in 'AdaBelief optimizer, adapting stepsizes by the 
    belief in observed gradients'_.
    Recommendation on hyper-parameters\:
    * For cases where SGD outperforms Adam (e.g. CNN for image classification), a large eps is recommended (1e-8)
    * For cases where Adam outperforms SGD (e.g. Transformer, GAN), a small eps is recommended (1e-16)
    * If weight_decouple == True, then the weight is scaled by (1 - lr * weight_decay).
    Note that default lr is different for Ada-optimizers and SGD, hence weight_decy needs to be rescaled.
    * For a full list of recommended hyper-parameters, see https://github.com/juntang-zhuang/Adabelief-Optimizer

    Arguments:
        params (iterable): iterable of parameters to optimize or dicts defining
            parameter groups
        lr (float, optional): learning rate (default: 1e-3)
        betas (Tuple[float, float], optional): coefficients used for computing
            running averages of gradient and its square (default: (0.9, 0.999))
        eps (float, optional): term added to the denominator to improve
            numerical stability (default: 1e-16)
        weight_decay (float, optional): weight decay (L2 penalty) (default: 0)
        amsgrad (boolean, optional): whether to use the AMSGrad variant of this
            algorithm from the paper `On the Convergence of Adam and Beyond`_
            (default: False)
        weight_decouple (boolean, optional):  If set as True, then
            the optimizer uses decoupled weight decay as in AdamW
            (default: True)
        rectify (boolean, optional): If set as True, then perform the rectified
            update similar to RAdam
            (default: True)
        degenerated_to_sgd (boolean, optional) If set as True, then perform SGD update
            when variance of gradient is high
            (default:True) 

    .. _AdaBelief optimizer, adapting stepsizes by the belief in observed gradients:
        https://arxiv.org/abs/2010.07468       
    .. _Adam\: A Method for Stochastic Optimization:
        https://arxiv.org/abs/1412.6980
    .. _Decoupled Weight Decay Regularization:
        https://arxiv.org/abs/1711.05101
    .. _On the Convergence of Adam and Beyond:
        https://openreview.net/forum?id=ryQu7f-RZ
    .. _On the Variance of Adam and Beyond:
        https://arxiv.org/abs/1908.03265
    """

    def __init__(self, params, lr=1e-3, betas=(0.9, 0.999), eps=1e-16,
                 weight_decay=0, amsgrad=False, weight_decouple=True, rectify=True,
                 degenerated_to_sgd=True):

        if not 0.0 <= lr:
            raise ValueError("Invalid learning rate: {}".format(lr))
        if not 0.0 <= eps:
            raise ValueError("Invalid epsilon value: {}".format(eps))
        if not 0.0 <= betas[0] < 1.0:
            raise ValueError("Invalid beta parameter at index 0: {}".format(betas[0]))
        if not 0.0 <= betas[1] < 1.0:
            raise ValueError("Invalid beta parameter at index 1: {}".format(betas[1]))

        self.degenerated_to_sgd = degenerated_to_sgd
        if isinstance(params, (list, tuple)) and len(params) > 0 and isinstance(params[0], dict):
            for param in params:
                if 'betas' in param and (param['betas'][0] != betas[0] or param['betas'][1] != betas[1]):
                    param['buffer'] = [[None, None, None] for _ in range(10)]

        defaults = dict(lr=lr, betas=betas, eps=eps,
                        weight_decay=weight_decay, amsgrad=amsgrad, buffer=[[None, None, None] for _ in range(10)])
        super(AdaBelief, self).__init__(params, defaults)

        self.degenerated_to_sgd = degenerated_to_sgd
        self.weight_decouple = weight_decouple
        self.rectify = rectify
        if self.weight_decouple:
            print('Weight decoupling enabled in AdaBelief')
        if self.rectify:
            print('Rectification enabled in AdaBelief')
        if amsgrad:
            print('AMSGrad enabled in AdaBelief')

    def __setstate__(self, state):
        super(AdaBelief, self).__setstate__(state)
        for group in self.param_groups:
            group.setdefault('amsgrad', False)

    @torch.no_grad()
    def step(self, closure=None):
        """Performs a single optimization step.

        Arguments:
            closure (callable, optional): A closure that reevaluates the model
                and returns the loss.
        """
        loss = None
        if closure is not None:
            with torch.enable_grad():
                loss = closure()

        for group in self.param_groups:
            amsgrad = group['amsgrad']

            grads = []
            states = []
            exp_avg = []
            exp_avg_sq = []
            max_exp_avg_sq = []
            params_with_grad = []

            for p in group['params']:
                if p.grad is not None:
                    if p.grad.is_sparse:
                        raise RuntimeError('AdaBelief does not support sparse gradients')

                    # perform weight decay, check if decoupled weight decay
                    if self.weight_decouple:
                        p.data.mul_(1.0 - group['lr'] * group['weight_decay'])
                    else:
                        if group['weight_decay'] != 0:
                            p.grad.add_(p.data, alpha=group['weight_decay'])

                    params_with_grad.append(p)
                    grads.append(p.grad)

            for p in params_with_grad:
                state = self.state[p]

                # State initialization
                if len(state) == 0:
                    state['step'] = 0
                    # Exponential moving average of gradient values
                    state['exp_avg'] = torch.zeros_like(p, memory_format=torch.preserve_format)
                    # Exponential moving average of squared gradient values
                    state['exp_avg_sq'] = torch.zeros_like(p, memory_format=torch.preserve_format)
                    if amsgrad:
                        # Maintains max of all exp. moving avg. of sq. grad. values
                        state['max_exp_avg_sq'] = torch.zeros_like(p, memory_format=torch.preserve_format)

                exp_avg.append(state['exp_avg'])
                exp_avg_sq.append(state['exp_avg_sq'])

                if amsgrad:
                    max_exp_avg_sq.append(state['max_exp_avg_sq'])

                state['step'] += 1
                states.append(state)

            beta1, beta2 = group['betas']

            bias_correction1 = [1 - beta1 ** state['step'] for state in states] 
            bias_correction2 = [1 - beta2 ** state['step'] for state in states] 

            #
            # Decay the first and second moment running average coefficient
            #
            torch._foreach_mul_(exp_avg, beta1)
            torch._foreach_add_(exp_avg, grads, alpha=1 - beta1)

            difs = torch._foreach_sub( grads, exp_avg )

            torch._foreach_mul_(exp_avg_sq, beta2)
            torch._foreach_addcmul_(exp_avg_sq, difs, difs, 1 - beta2)
            torch._foreach_add_(exp_avg_sq, group['eps'])

            if amsgrad:
                # Maintains the maximum of all 2nd moment running avg. till now
                max_exp_avg_sq = torch._foreach_maximum(max_exp_avg_sq, exp_avg_sq)

                # Use the max. for normalizing running avg. of gradient
                max_exp_avg_sq_sqrt = torch._foreach_sqrt(max_exp_avg_sq)
                bias_correction_sqrt = [math.sqrt(bc) for bc in bias_correction2]
                torch._foreach_div_(max_exp_avg_sq_sqrt, bias_correction_sqrt)
                denom = torch._foreach_add(max_exp_avg_sq_sqrt, group['eps'])
            else:
                exp_avg_sq_sqrt = torch._foreach_sqrt(exp_avg_sq)
                bias_correction_sqrt = [math.sqrt(bc) for bc in bias_correction2]
                torch._foreach_div_(exp_avg_sq_sqrt, bias_correction_sqrt)
                denom = torch._foreach_add(exp_avg_sq_sqrt, group['eps'])

            # update:
            if not self.rectify:
                step_size = [-1 * (group['lr'] / bc) for bc in bias_correction1]
                torch._foreach_addcdiv_(params_with_grad, exp_avg, denom, step_size)
            else: # rectified update
                N_smas, rectify_step_sizes = self.get_rectification_factor(group)
                # split list by rectification
                conf_params_with_grad, conf_exp_avg, conf_denom, conf_step_size = [], [], [], []
                inconf_params_with_grad, inconf_exp_avg, inconf_denom, inconf_step_size = [], [], [], []
                for (_param_with_grad, _exp_avg, _denom, _N_sma, _rec_step_size) in zip( params_with_grad, exp_avg, deno, N_smas, rectify_step_sizes):
                    if N_smas >= 5:
                        conf_params_with_grad.append(_param_with_grad)
                        conf_exp_avg.append(_exp_avg)
                        conf_denom.append(_denom)
                        conf_step_size.append( -group['lr'] * _rec_step_size )
                    elif _rec_step_size > 0.0:
                        inconf_params_with_grad.append(_param_with_grad)
                        inconf_exp_avg.append(_exp_avg)
                        inconf_denom.append(_denom)
                        inconf_step_size.append( -group['lr'] * _rec_step_size )
                
                # update parameters by confidence
                torch._foreach_addcdiv_(conf_params_with_grad, conf_exp_avg, conf_denom, conf_step_size) # Adam-type update
                torch._foreach_add_( inconf_params_with_grad, inconf_exp_avg, inconf_step_size ) # SGD-type update

        return loss
    
    def get_rectification_factor(self, group):
        rectify_step_sizes = []
        N_smas = [] 

        for p in group['params']:
            if p.grad is None:
                continue
            
            if p.grad.is_sparse:
                raise RuntimeError('AdaBelief does not support sparse gradients.')
            
            state = self.state[p]
        
            buffered = group['buffer'][int(state['step'] % 10)]
            if state['step'] == buffered[0]:
                N_sma, step_size = buffered[1], buffered[2]
            else:
                buffered[0] = state['step']
                beta2_t = beta2 ** state['step']
                N_sma_max = 2 / (1 - beta2) - 1
                N_sma = N_sma_max - 2 * state['step'] * beta2_t / (1 - beta2_t)
                buffered[1] = N_sma

                # more conservative since it's an approximated value
                if N_sma >= 5:
                    step_size = math.sqrt(
                                (1 - beta2_t) * (N_sma - 4) / (N_sma_max - 4) * (N_sma - 2) / N_sma * N_sma_max / (
                                        N_sma_max - 2)) / (1 - beta1 ** state['step'])
                elif self.degenerated_to_sgd:
                    step_size = 1.0 / (1 - beta1 ** state['step'])
                else:
                    step_size = -1
                buffered[2] = step_size

            N_smas.append(N_sma)
            rectify_step_sizes.append(step_size)
        return N_smas, rectify_step_sizes

    # TODO: refactor to a base class once foreach ops are in a good shape.
    def zero_grad(self, set_to_none: bool = False):
        per_device_and_dtype_grads = defaultdict(lambda: defaultdict(list))
        for group in self.param_groups:
            for p in group['params']:
                if p.grad is not None:
                    if set_to_none:
                        p.grad = None
                    else:
                        if p.grad.grad_fn is not None:
                            p.grad.detach_()
                        else:
                            p.grad.requires_grad_(False)

                        if p.grad.is_sparse:
                            p.grad.zero_()
                        else:
                            per_device_and_dtype_grads[p.grad.device][p.grad.dtype].append(p.grad)

            for _, per_dtype_grads in per_device_and_dtype_grads.items():
                for grads in per_dtype_grads.values():
                    torch._foreach_zero_(grads)
