import os
from pycparser.c_ast import Default
import zmq
import argparse
import pickle
import math
import numpy as np
from collections import deque
import random
import time
from tqdm import tqdm

import torch
from tensorboardX import SummaryWriter 

from queue import Queue, LifoQueue
from predict_trajectories.trajectory_loader import trajectory_loader
from interaction_env import InteractionEnv

from algo.bcq import BCQ
from algo.td3_bc import TD3_BC
from algo.DDPG import DDPG
from algo.bear import BEAR
from algo.VAEbc import VAEBC
from algo.cql import CQLSAC
from algo.iql import IQL
from algo.ddpg import DDPG_offline
# from algo.morel.morel import Morel

from config import hyperParameters
import ReplayBuffer


class main_loop(object):
    def __init__(self, sim_args):
        
        self.interface = InteractionEnv(sim_args)
        self.args = sim_args
        self.train_model = sim_args.train_model
        self.algo_name = sim_args.algo_name
        self.buffer_name = sim_args.buffer_name
        self.load_model = 0

        self.current_time = time.strftime("%Y-%m-%d_%H-%M", time.localtime())


        # config setting
        self.config = hyperParameters(control_steering=sim_args.control_steering)
        self.action_type = self.config.action_type     # speed or acc_steer

        # common setting
        random.seed(self.config.seed)
        torch.manual_seed(self.config.seed)
        np.random.seed(self.config.seed)
        self.state_dim = self.config.state_dim
        self.action_dim = self.config.action_dim
        self.max_action = self.config.max_action
        self.device = self.config.device

        # config for offline training
        self.offline_timesteps = int(self.config.offline_timesteps)
        self.offline_buffer_size = int(self.config.offline_buffer_size)

        # setting for print and save
        self.setting = f"action_type_{self.action_type}_seed_{self.config.seed}_buffer_name_{self.buffer_name}"



        print("---------------------------------------")
        if self.train_model == "offline":
            print(f"Train_model: {self.train_model}, Algo_name: {self.algo_name}, setting = {self.setting}")
        print("---------------------------------------")



    def train_offline(self):

        writer = SummaryWriter(f'./log/{self.algo_name}_{self.current_time}')

        # Initialize policy
        if self.algo_name == "BC":
            policy = VAEBC(self.state_dim, self.action_dim, self.max_action, self.device)
        elif self.algo_name =="BCQ":
            policy = BCQ(**self.config.BCQ_config)
        elif self.algo_name == "TD3_BC":
            policy = TD3_BC(**self.config.TD3_BC_config)
        elif self.algo_name == "BEAR":
            policy = BEAR(**self.config.BEAR_config)
        elif self.algo_name == "CQL":
            policy = CQLSAC(self.state_dim, self.action_dim,self.device)
        elif self.algo_name == "IQL":
            policy = IQL(**self.config.IQL_config)
        elif self.algo_name == "DDPG_offline":
            policy = DDPG_offline(self.state_dim, self.action_dim, self.max_action, self.device)
        # elif self.algo_name == "MOReL":
        #     policy = Morel(**self.config.Morel_config)

        
        # Load buffer
        print(f"Loading buffer: {self.buffer_name}")
        # EP0_human_expert_0  14118
        if self.buffer_name == "DDPG_1e5_new":
            self.offline_buffer_size = int(1e5)
            replay_buffer = ReplayBuffer.ReplayBuffer(self.state_dim, self.action_dim, self.device, self.offline_buffer_size)
            replay_buffer.load(f"./offlinedata/buffers/{self.buffer_name}")
            train_timesteps = self.offline_buffer_size
            self.config.eval_freq = int(5000)
        elif self.buffer_name == "CHN_human_expert_0_new":
            self.offline_buffer_size = int(108311)
            replay_buffer = ReplayBuffer.ReplayBuffer(self.state_dim, self.action_dim, self.device, self.offline_buffer_size)
            replay_buffer.load(f"./offlinedata/buffers/{self.buffer_name}")
            train_timesteps = self.offline_buffer_size
            self.config.eval_freq = int(5e3)
        elif self.buffer_name == "random_1e5_new":
            self.offline_buffer_size = int(1e5)
            replay_buffer = ReplayBuffer.ReplayBuffer(self.state_dim, self.action_dim, self.device, self.offline_buffer_size)
            replay_buffer.load(f"./offlinedata/buffers/{self.buffer_name}")
            train_timesteps = self.offline_buffer_size
            self.config.eval_freq = int(5e3)
        elif self.buffer_name == "only_random_10e3":
            self.offline_buffer_size = int(14346+10e3)
            replay_buffer = ReplayBuffer.ReplayBuffer(self.state_dim, self.action_dim, self.device, self.offline_buffer_size)
            buffer_name1 = 'CHN_human_expert_0_new'
            buffer_name2 = 'random_1e5_new'
            replay_buffer.load_buffers(f"./offlinedata/buffers/{buffer_name1}", f"./offlinedata/buffers/{buffer_name2}")
            train_timesteps =int(max(5e4,self.offline_buffer_size))
            self.config.eval_freq = int(5e3)
        elif self.buffer_name == "mix_random_75e3":
            self.offline_buffer_size = int(1e5)
            replay_buffer = ReplayBuffer.ReplayBuffer(self.state_dim, self.action_dim, self.device, self.offline_buffer_size)
            buffer_name1 = 'CHN_human_expert_0_new'
            buffer_name2 = 'random_1e5_new'
            replay_buffer.load_buffers(f"./offlinedata/buffers/{buffer_name1}", f"./offlinedata/buffers/{buffer_name2}")
            train_timesteps = self.offline_buffer_size
            self.config.eval_freq = int(5e3)
        elif self.buffer_name == "real_random6e4":
            self.offline_buffer_size = int(108311+6e4)
            replay_buffer = ReplayBuffer.ReplayBuffer(self.state_dim, self.action_dim, self.device, self.offline_buffer_size)
            buffer_name1 = 'CHN_human_expert_0_new'
            buffer_name2 = 'random_1e5_new'
            replay_buffer.load_buffers(f"./offlinedata/buffers/{buffer_name1}", f"./offlinedata/buffers/{buffer_name2}")
            train_timesteps = self.offline_buffer_size
            self.config.eval_freq = int(5e3)
        print("Loaded buffer")


        if self.args.visualaztion:
            print(f'use trained policy: {self.algo_name}_network_{self.setting}')
            policy.load(f"./models/{self.algo_name}/{self.algo_name}_network_{self.setting}")
            eval = self.eval_policy(policy)


        n_epochs = int(train_timesteps) // int(self.config.eval_freq)
        training_iters = 0
        results = dict()
        results['avg_reward'] = []
        results['success_rate'] = []
        results['time_exceed_rate'] = []
        results["collision_rate"] = []
        actor_loss = []
        critic_loss = []
        q_val =[]


        print("Initial eval")
        eval = self.eval_policy(policy)
        results['avg_reward'].append(eval['avg_reward'])
        results['success_rate'].append(eval['success_rate'])
        results['time_exceed_rate'].append(eval['time_exceed_rate'])
        results["collision_rate"].append(eval['collision_rate'])

        np.save(f"./results/{self.algo_name}_results_{self.setting}", results)

        # policy.record_performence(training_iters, avg_reward)
        writer.add_scalar(f'log/{self.algo_name}_avg_reward', float(eval['avg_reward']), training_iters)
        writer.add_scalar(f'log/{self.algo_name}_success_rate', float(eval['success_rate']), training_iters)
        writer.add_scalar(f'log/{self.algo_name}_collision_rate', float(eval['collision_rate']), training_iters)

        
        
       
        for epoch in range(n_epochs):
            # range_gen = tqdm(
            #     range(int(self.config.eval_freq)),
            #     desc=f"Epoch {int(epoch)}/{n_epochs}",
            range_gen = range(int(self.config.eval_freq))
        
            for itr in range_gen:
                info = policy.train(replay_buffer, batch_size = self.config.batch_size)
                
                actor_loss.append(info['actor_loss'])
                critic_loss.append(info['critic_loss'])
                q_val.append(info['q_val'])
                writer.add_scalar(f'log/{self.algo_name}_q_val', float(q_val[training_iters]), training_iters)
                writer.add_scalar(f'log/{self.algo_name}_actor_loss', float(actor_loss[training_iters]), training_iters)
                writer.add_scalar(f'log/{self.algo_name}_critic_loss', float(critic_loss[training_iters]), training_iters)

                training_iters += 1
                # print(training_iters)

            
            print("---------------------------------------")
            eval = self.eval_policy(policy)
            print("Epoch {}/{}, Train step: {}".format(epoch+1, n_epochs, training_iters))
            policy.save(f"./models/{self.algo_name}/{self.algo_name}_network_{self.setting}")
            # print(f'model has saved {self.algo_name}_network_{self.setting}')

        
            results['avg_reward'].append(eval['avg_reward'])
            results['success_rate'].append(eval['success_rate'])
            results['time_exceed_rate'].append(eval['time_exceed_rate'])
            results["collision_rate"].append(eval['collision_rate'])

            np.save(f"./results/{self.algo_name}_results_{self.setting}", results)

            # policy.record_performence(training_iters, avg_reward)
            writer.add_scalar(f'log/{self.algo_name}_avg_reward', float(eval['avg_reward']), training_iters)
            writer.add_scalar(f'log/{self.algo_name}_success_rate', float(eval['success_rate']), training_iters)
            writer.add_scalar(f'log/{self.algo_name}_collision_rate', float(eval['collision_rate']), training_iters)



    # Runs policy eval_episodes times and returns average reward, success_rate, collision_rate
    def eval_policy(self, policy):

        success = 0
        failure = 0
        collision = 0
        deflection = 0
        time_exceed = 0
        episode_num = 0
        avg_reward = 0

        
        # path for demonstration
        demonstration_dir = os.path.dirname(os.path.abspath(__file__))
        demonstration_dir = os.path.join(demonstration_dir, "offlinedata")
        demonstration_name = f"CHN_speed_demonstration_000"
        demonstration_path = os.path.join(demonstration_dir, "vehicle_demo", demonstration_name)
        with open(demonstration_path, 'rb') as fo:
            demo = pickle.load(fo, encoding='bytes')
            fo.close()

        while not self.interface.socket.closed and episode_num < self.config.eval_episodes:

            episode_reward = 0
            episode_step = 0
            episode_num += 1
            count = episode_num - 1  # count: [0, self.config.eval_episodes-1]

            # state reset 
            state_dict = self.interface.reset(count)

            ego_ids = []
            for i in range(102):
                if i+1 in [3,31]:
                    continue
                ego_ids.append(i+1)

            # prepare for no action
            for ego_id, ego_state in state_dict.items():
                initial_action  = np.array(ego_state)[8]/25
            
            # prepare for expert action 
            vehicle_id = ego_ids[count]
            # vehicle_id = 354
            traj = demo[vehicle_id]
            
                
            # start interaction and evaluate the policy 
            while True:
                # print(f'episode_step: {episode_step}')
                sars_tuple = traj[episode_step]
                expert_action = sars_tuple[2]
                offline_state = sars_tuple[1]
                
                action_dict = dict()
                for ego_id, ego_state in state_dict.items():
                    # policy action
                    action = policy.select_action(np.array(ego_state))
                    # print(f'policy_action: {action}')
  
                    # No action
                    # action = np.array([initial_action])
                    # print(f'initial_action: {initial_action}')

                    #  expert action
                    # action = expert_action
                    # print(f'expert_action: {expert_action}')

                    action_dict[ego_id] = list(action)

                    # error = offline_state - ego_state
                    # for i in range(len(error)):
                    #     error[i] = abs(error[i])
                    # max_error_index = np.argmax(error)
                    # print(f'max_index: {max_error_index},  max_error: {error[max_error_index]}')
                    # temp = np.linalg.norm(error)
                    # # print(f'error: {error}')
                    # print(f'difference between offline and env: {temp}')

                next_state_dict, reward_dict, done_dict, aux_info_dict = self.interface.step(action_dict)

                # episode reward ++
                reward = list(reward_dict.values())[0]
                episode_reward += reward
                avg_reward += reward
                
                if False not in done_dict.values():  # all egos are done
                    aux_info = list(aux_info_dict.values())[0]
                    if aux_info['result'] == 'collision':
                        collision += 1
                        failure += 1
                    elif aux_info['result'] == 'time_exceed':
                        time_exceed += 1
                        # success += 1
                    else:
                        success += 1
                    break
                else:
                    episode_step += 1
                    state_dict = next_state_dict
    
        # After run eval_episodes = 10 episodes, we get avg_reward
        avg_reward /= self.config.eval_episodes
        success_rate = success/self.config.eval_episodes
        time_exceed_rate = time_exceed/self.config.eval_episodes
        collision_rate = collision/self.config.eval_episodes
        eval = {
            'avg_reward': avg_reward,
            'success_rate' : success_rate,
            'time_exceed_rate': time_exceed_rate,
            'collision_rate': collision_rate
        }

        print(f"Evaluation over {self.config.eval_episodes} episodes: {avg_reward:.3f}, success_rate = {success_rate:.3f}, time_exceed_tate: {time_exceed_rate:.3f}, collision_rate = {collision_rate:.3f}")
        return eval



if __name__ == "__main__":
    # simulation argument
    sim_parser = argparse.ArgumentParser()
    sim_parser.add_argument("port", type=int, help="Number of the port (int)", default=5557, nargs="?")
    sim_parser.add_argument("scenario_name", type=str, default="DR_CHN_Merging_ZS" ,help="Name of the scenario (to identify map and folder for track "
                            "files), DR_CHN_Merging_ZS, DR_USA_Intersection_EP0", nargs="?")
    sim_parser.add_argument("total_track_file_number", type=int, help="total number of the track file (int)", default=7, nargs="?")
    sim_parser.add_argument("load_mode", type=str, help="Dataset to load (vehicle, pedestrian, or both)", default="vehicle", nargs="?")
    sim_parser.add_argument("demo_collecting", type=bool, help="Collecting demo through interacting with env", default=False, nargs="?")    
    sim_parser.add_argument("continous_action", type=bool, help="Is the action type continous or discrete", default=True,nargs="?")

    sim_parser.add_argument("control_steering", type=bool, help="control both lon and lat motions", default=False,nargs="?")
    sim_parser.add_argument("route_type", type=str, help="predict, ground_truth or centerline", default='ground_truth',nargs="?")

    sim_parser.add_argument("visualaztion", type=bool, help="Visulize or not", default=False,nargs="?")
    sim_parser.add_argument("ghost_visualaztion", type=bool, help="Visulize or not", default=True,nargs="?")
    sim_parser.add_argument("route_visualaztion", type=bool, help="Visulize or not", default=True,nargs="?")
    sim_parser.add_argument("route_bound_visualaztion", type=bool, help="Visulize or not", default=False,nargs="?")

    sim_parser.add_argument("is_imitation_saving_demo",type=bool, help="Collect demo or not", default=False,nargs="?")
    sim_parser.add_argument("is_imitation_agent",type=bool, help="Use agent policy or not", default=True,nargs="?")

    sim_parser.add_argument('--train_model', type = str, default="offline", help='offline, online, generate_buffer,EP0_human_expert_0')
    sim_parser.add_argument('--load_model', type = str, default="0", help='0 or 1')
    sim_parser.add_argument('--buffer_name', type = str, default="DDPG_1e5_new", help='mix, real_random2e4,mix_2e4,mix_1e5, CHN_human_expert_0, DDPG_CHN_0, random_CHN_0')
    # CHN_human_expert_0_new : terminal reward = 0 random_1e5_new  DDPG_CHN_0_new
    # real_random3e4: Real driving data + random data random_data 1e4->3e4->6e4;
    # mix_random_1e4： following donot change algo datasize-1e5, random_data 2.5e4->5e4->7.5e4
    # only_random_1e4  only_random_0
    sim_parser.add_argument('--algo_name', type = str, default="TD3_BC", help='DDPG_offline, No_action, BC, BCQ, BEAR, CQL, IQL, TD3_BC')


    sim_args = sim_parser.parse_args()

    if sim_args.scenario_name is None:
        raise IOError("You must specify a scenario. Type --help for help.")
    if sim_args.load_mode != "vehicle" and sim_args.load_mode != "pedestrian" and sim_args.load_mode != "both":
        raise IOError("Invalid load command. Use 'vehicle', 'pedestrian', or 'both'")


    if not os.path.exists("./results"):
        os.makedirs("./results")


    main_loop = main_loop(sim_args)
    main_loop.train_offline()
